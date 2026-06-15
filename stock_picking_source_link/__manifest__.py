# stock_picking_source_link/__manifest__.py
{
    "name": "Picking - Solicitud origen",
    "version": "19.0.1.0.0",
    "category": "Inventory",
    "summary": "Abre el pedido de venta desde una transferencia",
    "depends": ["sale_stock"],
    "data": [
        "views/stock_picking_views.xml",
    ],
    "license": "LGPL-3",
    "installable": True,
    "application": False,
}
