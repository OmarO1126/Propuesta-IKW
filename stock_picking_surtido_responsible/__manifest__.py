{
    "name": "Responsable de Surtido en Picking",
    "summary": "Asigna y controla el usuario responsable del surtido por pedido.",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Custom",
    "license": "LGPL-3",
    "depends": ["stock", "mail"],
    "data": [
        "security/security.xml",
        "views/stock_picking_views.xml",
        "data/stock_picking_server_actions.xml",
    ],
    "installable": True,
    "application": False,
}
