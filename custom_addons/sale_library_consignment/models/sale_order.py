from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    tiene_contactos_relacionados = fields.Boolean(
        string='Tiene Contactos Relacionados', 
        compute='_compute_tiene_contactos_relacionados'
    )
    
    consignado_domain_ids = fields.Many2many(
        'res.partner', 
        compute='_compute_consignado_domain_ids'
    )

    @api.depends('partner_id')
    def _compute_tiene_contactos_relacionados(self):
        for order in self:
            if order.partner_id:
                hijos_directos = self.env['res.partner'].search_count([('parent_id', '=', order.partner_id.id)])
                tiene_matriz = bool(order.partner_id.parent_id)
                order.tiene_contactos_relacionados = (hijos_directos > 0) or tiene_matriz
            else:
                order.tiene_contactos_relacionados = False

    @api.depends('partner_id')
    def _compute_consignado_domain_ids(self):
        for order in self:
            if not order.partner_id:
                order.consignado_domain_ids = [(6, 0, [])]
            else:
                matriz_id = order.partner_id.parent_id.id if order.partner_id.parent_id else order.partner_id.id
                familia = self.env['res.partner'].search([('parent_id', '=', matriz_id)])
                order.consignado_domain_ids = [(6, 0, familia.ids)]

    def action_ver_consignados(self):
        self.ensure_one()
        if not self.partner_id:
            return
        
        matriz_id = self.partner_id.parent_id.id if self.partner_id.parent_id else self.partner_id.id
        
        wizard = self.env['sale.consignado.wizard'].create({
            'order_id': self.id,
            'matriz_id': matriz_id,
        })
        
        view_kanban_id = self.env.ref('sale_library_consignment.view_sale_consignado_wizard_kanban').id
        view_search_id = self.env.ref('sale_library_consignment.view_sale_consignado_search').id
        
        return {
            'name': 'Seleccionar Consignado',
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'kanban',
            'views': [(view_kanban_id, 'kanban')],
            'search_view_id': [view_search_id],
            'domain': [('parent_id', '=', matriz_id)],
            'context': {'active_order_id': self.id, 'default_parent_id': matriz_id},
            'target': 'new',
        }


class SaleConsignadoWizard(models.TransientModel):
    _name = 'sale.consignado.wizard'
    _description = 'Asistente de Seleccion de Consignado'

    order_id = fields.Many2one('sale.order', string='Orden de Venta', required=True)
    matriz_id = fields.Many2one('res.partner', string='Matriz')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def seleccionar_consignado(self):
        """Método que se ejecuta al hacer clic en la tarjeta Kanban"""
        self.ensure_one()
        order_id = self.env.context.get('active_order_id')
        if not order_id:
            wizard = self.env['sale.consignado.wizard'].search([], order='id desc', limit=1)
            if wizard and wizard.order_id:
                order_id = wizard.order_id.id
        
        if order_id:
            order = self.env['sale.order'].browse(order_id)
            if order.exists():
                order.write({'partner_id': self.id})
                
        return {'type': 'ir.actions.act_window_close'}