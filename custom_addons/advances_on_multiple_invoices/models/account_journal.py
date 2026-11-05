from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    use_for_remission_payments = fields.Boolean(
        string="Usar para pagos de remisiones",
        help=(
            "Usa este diario bancario o de efectivo exclusivamente para pagos "
            "aplicados a documentos marcados como remisiones."
        ),
    )
