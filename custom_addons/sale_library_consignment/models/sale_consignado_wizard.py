from odoo import models, fields, api

class SaleConsignadoWizard(models.TransientModel):
    _name = 'sale.consignado.wizard'
    _description = 'Asistente de Seleccion de Consignado'

    order_id = fields.Many2one('sale.order', string='Orden de Venta', required=True)
    matriz_id = fields.Many2one('res.partner', string='Matriz')
    
    # Campo relacional que contendrá las sucursales filtradas de la matriz
    consignado_ids = fields.Many2many(
        'res.partner', 
        string='Consignados',
        compute='_compute_consignado_ids'
    )

    @api.depends('matriz_id')
    def _compute_consignado_ids(self):
        for wizard in self:
            if wizard.matriz_id:
                hijos = self.env['res.partner'].search([('parent_id', '=', wizard.matriz_id.id)])
                wizard.consignado_ids = [(6, 0, hijos.ids)]
            else:
                wizard.consignado_ids = [(6, 0, [])]

    def seleccionar_consignado(self, partner_id):
        """Método que se ejecuta al hacer clic en una tarjeta Kanban para asignarla a la orden"""
        self.ensure_one()
        partner = self.env['res.partner'].browse(partner_id)
        if partner.exists() and self.order_id:
            self.order_id.write({'partner_id': partner.id})
        return {'type': 'ir.actions.act_window_close'}