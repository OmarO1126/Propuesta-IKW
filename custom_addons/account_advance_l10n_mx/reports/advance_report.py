from odoo import models, api
from collections import defaultdict

class ReportAdvanceSummary(models.AbstractModel):
    _name = 'report.account_advance_l10n_mx.report_advance_template'
    _description = 'Reporte de Anticipos PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        form_data = data.get('form', {})
        
        partner_id = form_data.get('partner_id')
        agent_id = form_data.get('agent_id')
        date_from = form_data.get('date_from')
        date_to = form_data.get('date_to')
        report_type = form_data.get('report_type', 'general')

        # Dominio base
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('is_advance_invoice', '=', True)
        ]
        
        # Filtro de cliente directo
        if partner_id:
            domain.append(('partner_id', '=', partner_id))
            
        # Filtro de Agente con lógica de Matrices y Sucursales (Hijos)
        if agent_id:
            main_partners_agent = self.env['res.partner'].search([
                ('parent_id', '=', False),
                ('is_company', '=', True),
                ('x_studio_vendedor', '=', agent_id)
            ])
            
            if main_partners_agent:
                sub_partners_agent = self.env['res.partner'].search([('parent_id', 'in', main_partners_agent.ids)])
                valid_agent_partners = main_partners_agent | sub_partners_agent
                domain.append(('partner_id', 'in', valid_agent_partners.ids))
            else:
                domain.append(('partner_id', '=', False))
                
        # Filtros de fecha
        if date_from:
            domain.append(('invoice_date', '>=', date_from))
        if date_to:
            domain.append(('invoice_date', '<=', date_to))

        advances = self.env['account.move'].search(domain, order='partner_id, invoice_date')

        # Precargar las líneas de los anticipos
        lines_by_advance = defaultdict(list)
        if advances:
            custom_lines = self.env['advance.invoice.line'].search([('advance_id', 'in', advances.ids)])
            for line in custom_lines:
                lines_by_advance[line.advance_id.id].append({
                    'name': line.invoice_id.name,
                    'date': line.invoice_id.invoice_date,
                    'amount': line.amount,
                    'total_with_tax': line.total_with_tax,
                })

        # Agrupar y calcular saldos restando el total con impuesto
        grouped_advances = defaultdict(list)
        for adv in advances:
            partner = adv.partner_id
            applied_invoices = lines_by_advance.get(adv.id, [])
            
            amount_total = adv.amount_total
            
            # Sumamos el 'total_with_tax' de las aplicaciones para calcular lo realmente aplicado
            total_applied = sum(inv['total_with_tax'] for inv in applied_invoices)
            balance_left = amount_total - total_applied

            grouped_advances[partner].append({
                'move': adv,
                'amount_total': amount_total,
                'total_applied': total_applied,
                'balance_left': balance_left,
                'applied_invoices': applied_invoices if report_type == 'detailed' else []
            })

        return {
            'doc_ids': docids,
            'doc_model': 'advance.report.wizard',
            'data': form_data,
            'grouped_advances': dict(grouped_advances),
        }