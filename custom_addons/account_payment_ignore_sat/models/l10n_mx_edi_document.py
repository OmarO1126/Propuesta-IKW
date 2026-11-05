from odoo import models, api

class L10nMxEdiDocument(models.Model):
    _inherit = 'l10n_mx_edi.document'

    @api.model_create_multi
    def create(self, vals_list):
        """ Bloquea la creación de documentos EDI/Complementos de pago 
            si el pago relacionado tiene activo el booleano x_ignore_sat_payment.
        """
        filtered_vals_list = []
        for vals in vals_list:
            move_id = vals.get('move_id')
            if move_id:
                move = self.env['account.move'].browse(move_id)
                # Verificamos si el registro tiene el campo y está en True
                if getattr(move, 'x_ignore_sat_payment', False):
                    continue
            filtered_vals_list.append(vals)
            
        if not filtered_vals_list:
            return self.env['l10n_mx_edi.document']
            
        return super().create(filtered_vals_list)