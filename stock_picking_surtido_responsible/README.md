# Responsable de Surtido en Picking

Personalizacion para Odoo 19 que usa el campo estandar `user_id` de `stock.picking`
como **Responsable de surtido**.

## Que hace

- Muestra `Responsable de surtido` en el formulario y lista de pickings.
- Agrega filtros: `Mis surtidos`, `Sin responsable de surtido`, `Surtidos vencidos`.
- Agrega accion masiva: `Asignarme como responsable de surtido`.
- Notifica en chatter y crea actividad al usuario asignado.
- Bloquea la validacion de entregas salientes e internas sin responsable.
- Permite validar solo al responsable asignado, salvo usuarios autorizados.
- Hereda el responsable al crear backorders.

## Alcance

Por defecto aplica a:

- Entregas salientes: `outgoing`
- Transferencias internas: `internal`

Si tambien debe aplicar a recepciones de compras, cambia en
`models/stock_picking.py`:

```python
return ("outgoing", "internal")
```

por:

```python
return ("incoming", "outgoing", "internal")
```

## Grupos

- `Picking+: Asignar responsable de surtido`
- `Picking+: Validar surtidos de otros usuarios`

Los administradores de inventario pueden hacer ambas acciones.

## Instalacion en Odoo.sh

1. Copiar la carpeta `stock_picking_surtido_responsible` dentro del repositorio de addons personalizados.
2. Subir cambios a la rama de Odoo.sh.
3. Actualizar lista de aplicaciones.
4. Instalar `Responsable de Surtido en Picking`.
5. Asignar grupos a los usuarios de almacen.
