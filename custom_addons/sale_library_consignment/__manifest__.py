{
    'name': 'Venta, Biblioteca y Consignados',
    'version': '1.0',
    'summary': 'Módulo para gestionar libros y contactos consignados en órdenes de venta',
    'category': 'Sales',
    'author': 'bizin',
    'depends': ['base', 'sale'],
    'license': 'LGPL-3',
    'data': [
        'views/sale_order_views.xml',
        'security/ir.model.access.csv',
        
    ],
    'installable': True,
    'application': False,
}
