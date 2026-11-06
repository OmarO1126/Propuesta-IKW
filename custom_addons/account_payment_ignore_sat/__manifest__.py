{
    'name': 'Ignorar Timbrado SAT en Pagos',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'Permite marcar pagos para que sean ignorados y no se timbren ante el SAT.',
    'author': 'bizin',
    'depends': ['account', 'l10n_mx_edi'],
    'data': [
        'views/account_payment_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}