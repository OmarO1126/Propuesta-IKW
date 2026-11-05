# Distribución de pagos en múltiples facturas para Odoo 19

Addon para registrar **un solo pago de cliente** y distribuirlo, con importes elegidos por el usuario, entre varias facturas pendientes o parcialmente pagadas.

El módulo no reemplaza el motor fiscal de Odoo. Crea pagos `account.payment` estándar, divide sus contrapartidas de cuentas por cobrar y concilia cada división con la factura indicada. Cuando hay varias formas, extiende la plantilla oficial de `l10n_mx_edi` para repetir el nodo `Pago`, conservando sus cálculos de documentos relacionados, impuestos, parcialidades y saldos.

## Funcionalidad

- Un pago bancario o de caja para varias facturas del mismo cliente.
- Hasta tres formas de pago dentro del mismo cobro; la segunda y tercera se muestran sólo cuando el usuario las agrega.
- Cada forma genera un `account.payment` estándar en su diario y todos quedan vinculados al pago principal.
- Para facturas PPD, las formas se consolidan en un solo CFDI de Pago 2.0 con un nodo `Pago` por forma, importes e impuestos acumulados y parcialidades consecutivas.
- Distribución manual exacta por factura y por forma de pago, con una tabla independiente para cada método.
- Distribución automática por antigüedad, por facturas recientes o proporcional.
- Selector múltiple por número o referencia para mostrar y distribuir sólo las facturas elegidas, sin perder los importes capturados en otras.
- Cada lista de distribución tiene un scroll propio y una altura aproximada de quince renglones.
- Selector independiente entre **Facturas** y **Remisiones**; un mismo pago nunca puede mezclar ambos tipos.
- Las remisiones se identifican con el campo Studio `x_studio_es_una_remision` (con compatibilidad temporal para el nombre anterior terminado en `_1`).
- Los pagos de remisiones usan exclusivamente un diario bancario o de efectivo marcado como **Usar para pagos de remisiones**.
- Las remisiones generan su pago y conciliación contable, pero nunca generan CFDI ni complemento de pago.
- Las cotizaciones marcadas como remisión usan un único contacto emisor, identificado por la referencia interna `EMISOR-REMISION`. El formato de entrega toma la razón social, RFC y domicilio actuales de ese contacto, sin crear otra empresa en Odoo.
- Las facturas generadas desde remisiones usan el diario de ventas **Remisiones** de su empresa. Si falta, la creación se detiene con un aviso en lugar de elegir otro diario.
- Manejo de importes no aplicados: tanto un excedente sobre el saldo total como una diferencia deliberada —por ejemplo centavos que no se desean aplicar a otra factura— puede conservarse como saldo a favor o registrarse en una cuenta contable elegida.
- Perdón explícito de un faltante menor a 1.00 por factura: el dinero recibido queda en el pago real y los centavos se registran por separado en una cuenta de gasto (sugerida: `602.84.2`, si existe en la empresa). No se aplica a remisiones ni a facturas PUE mexicanas.
- Registro de un pagador distinto del cliente de las facturas y de su cuenta bancaria ordenante, sin cambiar la deuda que se concilia.
- Soporte para facturas con saldo parcial.
- Un mismo cobro puede liquidar facturas PUE y abonar total o parcialmente facturas PPD.
- Validación concurrente del saldo al confirmar.
- Registro auditable de la distribución dentro del pago.
- Recibo de pago con el número de operación debajo del importe pagado, así como el pagador, su RFC y las cuentas bancarias ordenante y receptora.
- Integración nativa con la localización mexicana de Odoo.
- Acceso desde **Contabilidad > Clientes > Distribuir pago de cliente**.
- Acción **Distribuir pago** en la lista de facturas de cliente.

## Requisitos

- Odoo Enterprise 19.0.
- `account_accountant`.
- `l10n_mx_edi`.
- El método de pago del diario debe tener configurada una cuenta de cobros pendientes.

## Instalación

1. Clona este repositorio dentro de una ruta incluida en `addons_path` usando el nombre técnico `advances_on_multiple_invoices`.
2. Actualiza la lista de aplicaciones.
3. Instala **Distribución de pagos en múltiples facturas (MX)**.

Por línea de comandos:

```bash
odoo-bin -d BASE_DE_DATOS -i advances_on_multiple_invoices --stop-after-init
```

## Flujo de uso

1. Para remisiones, abre el contacto **Emisor de remisiones (editar datos)**, identificado por `EMISOR-REMISION`, y captura su razón social y domicilio reales. Desde una cotización de remisión puedes abrirlo con **Editar contacto emisor**. Abre además el diario bancario o de efectivo que recibirá esos cobros y activa **Usar para pagos de remisiones**. Ese diario quedará reservado para este flujo.
2. Abre **Contabilidad > Clientes > Distribuir pago de cliente**, o selecciona varios documentos y pulsa **Distribuir pago**.
3. Elige **Facturas** o **Remisiones**. Si se abre desde documentos seleccionados, el asistente detecta el tipo y bloquea una selección mixta.
4. Captura el pagador, fecha y la primera forma de pago con su importe, diario, método, cuenta ordenante y número de operación. La forma de pago SAT y el origen CFDI sólo se solicitan para facturas.
5. Si el cobro combina transferencia, compensación u otra forma, pulsa **Agregar otra forma de pago**. Puede capturarse una tercera forma; el asistente muestra el total acumulado.
6. En **Documentos a mostrar**, busca y agrega varias facturas por número o referencia; pulsa **Mostrar seleccionados** para trabajar con ellas juntas, o **Mostrar todos** para regresar a la lista completa. Cada lista muestra unos quince renglones y se desplaza con su propio scroll. Abre la pestaña de cada método y captura el importe que aplicará a cada documento, o utiliza su distribución automática. Con el filtro activo, los botones automáticos sólo distribuyen entre los documentos mostrados y conservan aplicaciones previas en los demás. Las pestañas 2 y 3 muestran lo aplicado previamente y el saldo disponible restante.
7. Si queda una diferencia sin aplicar, activa **Registrar importe no aplicado** cuando corresponda y elige entre conservarla como saldo a favor o registrarla en una cuenta contable. La cuenta concreta debe seguir la política contable de la empresa.
8. Si faltaron centavos para liquidar una factura PPD y no se cobrarán, marca **No cobrar centavos** en su línea. Verifica la cuenta de gasto y el diario de ajuste; el asistente sugiere `602.84.2` sólo si ya existe en la compañía. Esta opción es distinta del importe no aplicado: requiere distribuir íntegramente lo que sí se recibió.
9. Confirma cuando el importe aplicable por distribuir sea cero.
10. Para facturas PPD, abre cualquiera de ellas y utiliza el flujo estándar **Actualizar pagos** para generar el complemento. Las remisiones y las facturas PUE quedan excluidas del `DoctoRelacionado`.

## Reglas y alcance inicial

- Sólo procesa facturas de cliente (`out_invoice`).
- El campo Studio `x_studio_es_una_remision` separa los documentos internos de las facturas fiscales. El módulo no permite pagarlos juntos.
- Un diario marcado para remisiones no aparece al pagar facturas normales y un diario normal no aparece al pagar remisiones.
- El recibo de una remisión se identifica como documento contable interno, muestra como receptor al titular configurado en la cuenta bancaria del diario y aclara la empresa que registró el pago.
- Todas las facturas deben pertenecer a la misma empresa, cliente comercial, moneda y cuenta por cobrar.
- El pagador puede ser distinto del cliente de las facturas. Contablemente, el pago se concilia contra la cuenta por cobrar y liquida la deuda del cliente facturado.
- Para efectos del CFDI de Pago, el receptor fiscal continúa siendo el cliente de las facturas relacionadas. El pagador y su cuenta ordenante se conservan como información operativa y bancaria del cobro; la cuenta ordenante se informa en el REP cuando la forma de pago lo permite.
- El RFC del pagador mostrado en el recibo es informativo: no sustituye el RFC receptor del CFDI ni el RFC de la institución financiera ordenante en el REP.
- Cuando el pago no supera el saldo cargado, por omisión todo el importe debe distribuirse entre facturas. El usuario puede autorizar explícitamente una diferencia no aplicada y enviarla a saldo a favor o a una cuenta contable.
- Cuando el pago supera el saldo cargado, todas las facturas deben liquidarse. El excedente puede quedar como saldo abierto a favor del cliente o contabilizarse en una cuenta seleccionada.
- En el REP sólo se relacionan los importes efectivamente aplicados a facturas PPD; una diferencia enviada a una cuenta contable no reduce el saldo de otra factura.
- En un faltante perdonado, el ajuste de gasto liquida el saldo contable, pero el REP conserva el importe realmente pagado y muestra los centavos restantes en `ImpSaldoInsoluto`. No se altera ni se vuelve a timbrar automáticamente el CFDI de ingreso; la política fiscal para condonar ese saldo debe validarla el responsable fiscal de la empresa.
- Un excedente abierto deja el pago parcialmente sin conciliar; por ello Odoo retiene el REP de facturas PPD hasta que ese saldo a favor se aplique. Al registrar el excedente en una cuenta no conciliable, el REP puede continuar por el importe efectivamente aplicado a las facturas.
- En empresas mexicanas, exige que todas las facturas tengan un CFDI timbrado válido.
- Las facturas PUE sólo se admiten cuando este pago las liquida totalmente, se registra a más tardar el último día del mes de la factura y usa la misma forma de pago SAT del CFDI. Se concilian contablemente, pero no se incluyen en el complemento.
- Las facturas PPD pueden recibir pagos totales o parciales. Sólo las PPD se incluyen como `DoctoRelacionado` en el complemento de pago.
- La pestaña **CFDI** de una factura PUE oculta los registros de complemento de pago asociados sólo para seguimiento interno; su CFDI de ingreso permanece visible. En las facturas PPD el complemento continúa visible.
- Todas las facturas PPD incluidas en el complemento deben tener el mismo RFC receptor.
- Una combinación de transferencia y compensación se informa con claves SAT independientes (por ejemplo, `03` y `17`) dentro de nodos `Pago` separados. Nunca se inventa una forma de pago combinada.
- Si el cobro incluye facturas PUE, cada forma que tenga un importe aplicado a esas facturas debe coincidir con la forma de pago timbrada; de lo contrario el asistente bloquea la operación.
- Al cancelar o restablecer a borrador cualquier componente de un cobro con varias formas, el módulo ejecuta la operación sobre el grupo contable completo. La solicitud fiscal de cancelación del REP continúa usando el flujo estándar del SAT/Odoo sobre el pago principal.
- La primera versión no mezcla monedas dentro del mismo pago. Odoo seguirá calculando diferencias cambiarias cuando el pago y las facturas estén en la misma divisa extranjera.

## Pruebas

Las pruebas automatizadas verifican que:

- se cree un pago contable por cada forma y todos queden vinculados a un solo cobro principal;
- la distribución del segundo y tercer método respete y muestre lo ya aplicado por los métodos anteriores;
- agregar métodos adicionales no duplique las facturas del asistente;
- una diferencia pequeña pueda registrarse en una cuenta aunque todavía existan otras facturas abiertas;
- cancelar un componente cancele todos los pagos contables del grupo y revierta sus conciliaciones;
- cada factura reciba exactamente el importe indicado;
- la contrapartida del pago quede completamente conciliada;
- no pueda publicarse un pago parcialmente distribuido;
- las distribuciones automáticas no excedan el saldo disponible;
- un sobrepago deje un crédito abierto o genere una línea en la cuenta de diferencia seleccionada, sin alterar los importes aplicados a facturas;
- el CFDI de Pago 2.0 contenga un `DoctoRelacionado` por factura PPD, con su UUID e importe pagado correctos.
- un pago mixto liquide completamente una factura PUE, abone parcialmente una PPD y genere el REP únicamente para la PPD.
- dos formas de pago generen un solo REP con dos nodos `Pago`, formas SAT independientes, importes acumulados y parcialidades consecutivas.

Ejecuta las pruebas con una instalación de Odoo 19 Enterprise que incluya este addon:

```bash
odoo-bin -d BASE_DE_PRUEBAS -i advances_on_multiple_invoices \
  --test-enable --test-tags /advances_on_multiple_invoices --stop-after-init
```

# Póliza mensual y recibo ampliado

- Cada cobro publicado recibe un consecutivo de póliza de cinco dígitos, único
  por empresa y mes, compartido por todas las formas de un cobro distribuido.
  La numeración se reinicia en `00001` al comenzar cada mes; cancelar y volver
  a publicar conserva el número. Las pólizas antiguas no se renumeran.
- El asistente sugiere una observación con el código del diario de pago y la
  fecha `DDMMAA` (por ejemplo `STD 051026`). Se puede editar en el asistente
  y después en el pago. El número de operación sigue siendo un campo distinto.
- El botón **Imprimir póliza** y el menú Imprimir del pago generan una póliza
  interna con el desglose por forma de pago y documento, incluidos importes
  no aplicados.
- El recibo muestra UUID, tipo de comprobante, folio y fecha del pago cuando
  hay complemento timbrado. Cada factura relacionada muestra además su UUID,
  folio, emisión y vencimiento. Para remisiones, el recibo identifica el
  contacto emisor y declara que el documento no es un CFDI.
