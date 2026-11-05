{
    'name': 'Despacho Prioritario',
    'version': '1.0',
    'author': 'Bizin',
    'category': 'Inventory/Delivery',
    'summary': 'Agrega control de despacho prioritario en ventas y transferencias',
    'description': 'Impide validar órdenes de entrega si existen órdenes anteriores pendientes, a menos que tengan la casilla de despacho prioritario activa.',
    'depends': ['sale_management', 'stock'],
    'data': [
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}