import base64
from odoo import models, fields, api

class AdvanceReportWizard(models.TransientModel):
    _name = 'advance.report.wizard'
    _description = 'Asistente para Reporte de Anticipos'

    report_type = fields.Selection([
        ('general', 'General (Solo totales por cliente)'),
        ('detailed', 'Detallado (Desglose por factura)')
    ], string="Tipo de Reporte", default='detailed', required=True)

    partner_id = fields.Many2one('res.partner', string="Cliente", help="Filtrar por cliente (opcional)")
    date_from = fields.Date(string="Fecha Desde")
    date_to = fields.Date(string="Fecha Hasta")

    report_pdf = fields.Binary(string="Vista Previa PDF", readonly=True)
    report_name = fields.Char(string="Nombre Archivo", default="Reporte_Anticipos.pdf")
    show_preview = fields.Boolean(string="Mostrar Vista Previa", default=False)
    
    # Campo renombrado para evitar el error ForeignKeyViolation
    vendedor_id = fields.Many2one(
        'hr.employee',
        string='Agente / Vendedor',
        domain="['|', ('work_contact_id.category_id.name', 'ilike', 'Vendedor'), ('user_id.partner_id.category_id.name', 'ilike', 'Vendedor')]",
        help="Selecciona un agente que tenga la etiqueta Vendedor en su contacto."
    )

    def _get_report_data(self):
        """Prepara el diccionario de datos para enviar al reporte QWeb"""
        return {
            'form': {
                'report_type': self.report_type,
                'partner_id': self.partner_id.id if self.partner_id else False,
                'partner_name': self.partner_id.name if self.partner_id else 'Todos',
                # Enviamos el nuevo campo pero conservando la llave 'agent_id' para el reporte
                'agent_id': self.vendedor_id.id if self.vendedor_id else False,
                'agent_name': self.vendedor_id.name if self.vendedor_id else 'Todos',
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }

    def action_preview(self):
        self.ensure_one()
        data = self._get_report_data()
        
        action_report = self.env.ref('account_advance_l10n_mx.action_report_advance_summary')
        pdf_content = action_report._render_qweb_pdf(action_report.report_name, self.ids, data=data)[0]
        
        self.write({
            'report_pdf': base64.b64encode(pdf_content),
            'show_preview': True
        })
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'advance.report.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_generate_report(self):
        self.ensure_one()
        return self.env.ref('account_advance_l10n_mx.action_report_advance_summary').report_action(self, data=self._get_report_data())