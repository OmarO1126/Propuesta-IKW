{
    'name': 'Gestión y Aplicación de Anticipos (Método 1 - SAT)',
    'version': '19.0.1.0.0', # (Ajusta la versión según la versión de Odoo que uses, ej. 16.0 o 17.0)
    'category': 'Accounting/Localizations',
    'summary': 'Módulo para la aplicación y control de anticipos con Notas de Crédito y relación CFDI 07',
    'author': 'biz-in',
    'depends': [
        'account',
        'l10n_mx_edi',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizards/apply_advance_invoice_wizard_views.xml',
        'views/account_move_views.xml',
        'wizards/advance_report_wizard_views.xml',  # <-- Agrega la vista del nuevo wizard de reporte
        'reports/advance_report_template.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
