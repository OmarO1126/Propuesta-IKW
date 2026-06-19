from odoo import _, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _surtido_picking_type_codes(self):
        """Operation types that require a warehouse picker assignment."""
        return ("outgoing", "internal")

    def _is_surtido_picking(self):
        return self.filtered(
            lambda picking: picking.picking_type_code in picking._surtido_picking_type_codes()
        )

    def _can_assign_surtido_responsible(self):
        return (
            self.env.user.has_group("stock.group_stock_manager")
            or self.env.user.has_group(
                "stock_picking_surtido_responsible.group_picking_surtido_assigner"
            )
        )

    def _can_validate_any_surtido(self):
        return (
            self.env.user.has_group("stock.group_stock_manager")
            or self.env.user.has_group(
                "stock_picking_surtido_responsible.group_picking_surtido_validate_any"
            )
        )

    def write(self, vals):
        assigning_responsible = (
            "user_id" in vals
            and not self.env.context.get("skip_surtido_assignment_check")
        )
        if assigning_responsible:
            active_surtidos = self.filtered(
                lambda picking: picking.state not in ("done", "cancel")
            )._is_surtido_picking()
            assigning_self = vals.get("user_id") == self.env.user.id
            if active_surtidos and not assigning_self and not self._can_assign_surtido_responsible():
                raise UserError(
                    _(
                        "Solo un responsable autorizado de almacén puede asignar "
                        "o cambiar el responsable de surtido."
                    )
                )
            previous_users = {picking.id: picking.user_id for picking in self}
        else:
            previous_users = {}

        result = super().write(vals)

        if assigning_responsible and vals.get("user_id"):
            self._notify_new_surtido_responsible(previous_users)

        return result

    def button_validate(self):
        surtidos = self.filtered(lambda picking: picking.state != "done")._is_surtido_picking()
        missing_responsible = surtidos.filtered(lambda picking: not picking.user_id)
        if missing_responsible:
            raise UserError(
                _(
                    "No puedes validar el surtido sin asignar responsable. "
                    "Pickings pendientes: %s"
                )
                % ", ".join(missing_responsible.mapped("name"))
            )

        if not self._can_validate_any_surtido():
            not_assigned_to_user = surtidos.filtered(
                lambda picking: picking.user_id != self.env.user
            )
            if not_assigned_to_user:
                raise UserError(
                    _(
                        "Solo el responsable de surtido asignado puede validar "
                        "este picking. Pickings bloqueados: %s"
                    )
                    % ", ".join(not_assigned_to_user.mapped("name"))
                )

        return super().button_validate()

    def action_assign_surtido_to_me(self):
        self.filtered(lambda picking: picking.state not in ("done", "cancel")).write(
            {"user_id": self.env.user.id}
        )
        return True

    def _notify_new_surtido_responsible(self, previous_users):
        model_id = self.env["ir.model"]._get_id("stock.picking")
        todo_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)

        for picking in self.filtered(
            lambda p: p.user_id and p.state not in ("done", "cancel")
        )._is_surtido_picking():
            if previous_users.get(picking.id) == picking.user_id:
                continue

            body = _(
                "Se asignó a %(user)s como responsable de surtido del picking %(picking)s."
            ) % {
                "user": picking.user_id.name,
                "picking": picking.name,
            }
            picking.message_post(
                body=body,
                partner_ids=picking.user_id.partner_id.ids,
                subtype_xmlid="mail.mt_note",
            )

            if todo_type:
                existing_activity = self.env["mail.activity"].search(
                    [
                        ("res_model_id", "=", model_id),
                        ("res_id", "=", picking.id),
                        ("activity_type_id", "=", todo_type.id),
                        ("user_id", "=", picking.user_id.id),
                    ],
                    limit=1,
                )
                if not existing_activity:
                    picking.activity_schedule(
                        "mail.mail_activity_data_todo",
                        fields.Date.context_today(picking),
                        summary=_("Surtir pedido"),
                        note=_("Tienes asignado este picking para surtido."),
                        user_id=picking.user_id.id,
                    )

    def _create_backorder(self, backorder_moves=None):
        backorders = super()._create_backorder(backorder_moves=backorder_moves)
        for backorder in backorders.filtered(lambda b: b.backorder_id and not b.user_id):
            if backorder.backorder_id.user_id:
                backorder.with_context(skip_surtido_assignment_check=True).write(
                    {"user_id": backorder.backorder_id.user_id.id}
                )
        return backorders
