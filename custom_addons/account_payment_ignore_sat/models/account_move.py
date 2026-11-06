from odoo import models

class AccountMove(models.Model):
    _inherit = 'account.move'

    def _l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values(self):
        """ Excluye de manera estricta los pagos marcados con x_ignore_sat_payment 
            para que no se envíen al PAC ni generen complementos.
        """
        results = super()._l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values()
        
        # Filtramos las llaves del diccionario (los pagos) que tengan activo el booleano
        payments_to_ignore = []
        for payment in results.keys():
            # Verificamos tanto en el pago directo como en su origen
            if getattr(payment, 'x_ignore_sat_payment', False) or getattr(payment.origin_payment_id, 'x_ignore_sat_payment', False):
                payments_to_ignore.append(payment)
                
        for pay in payments_to_ignore:
            results.pop(pay, None)
            
        return results

    def _l10n_mx_edi_cfdi_invoice_get_payments_diff(self):
        """ Filtra las diferencias de pagos para evitar que el sistema detecte 
            como pendiente de actualizar o procesar los pagos ignorados.
        """
        results = super()._l10n_mx_edi_cfdi_invoice_get_payments_diff()
        
        if results.get('to_process'):
            filtered_to_process = []
            for payment, pay_results in results['to_process']:
                if not getattr(payment, 'x_ignore_sat_payment', False) and not getattr(payment.origin_payment_id, 'x_ignore_sat_payment', False):
                    filtered_to_process.append((payment, pay_results))
            results['to_process'] = filtered_to_process
            
        return results