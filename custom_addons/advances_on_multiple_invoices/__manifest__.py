{
    "name": "Distribución de pagos en múltiples facturas (MX)",
    "summary": "Registra un pago y distribúyelo de forma exacta entre varias facturas",
    "description": """
Distribución de cobros de clientes entre varias facturas o remisiones.
Admite hasta tres formas de pago y genera un solo complemento Pagos 2.0
con nodos Pago independientes cuando corresponde.
    """,
    "version": "19.0.1.41.0",
    "category": "Accounting/Accounting",
    "author": "Business Insights MX",
    "website": "https://github.com/businessinsights-mx/advances_on_multiple_invoices",
    "license": "LGPL-3",
    "depends": [
        "account_accountant",
        "l10n_mx_edi",
        "sale_stock",
    ],
    "assets": {
        "web.assets_backend": [
            "advances_on_multiple_invoices/static/src/scss/payment_wizard.scss",
        ],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/remission_issuer_contact.xml",
        "wizard/multiple_invoice_payment_wizard_views.xml",
        "views/account_journal_views.xml",
        "views/account_move_views.xml",
        "views/account_payment_views.xml",
        "views/remission_sale_stock_views.xml",
        "views/l10n_mx_edi_payment_template.xml",
        "views/report_payment_receipt.xml",
        "views/report_payment_policy.xml",
        "views/report_remission_delivery.xml",
        "views/account_menus.xml",
    ],
    "installable": True,
    "application": False,
}
