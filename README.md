### Edinova

Consumo de ordnes de venta por parte de walmart

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app edinova
```

Después de instalar, abra **Edinova > Descuentos Negociados** y use el botón
**Asistente de configuración**. Seleccione la empresa, el cliente, el impuesto y
la sucursal que atenderá todos los pedidos. El asistente muestra una vista previa
y sólo permite aplicar la plantilla cuando todos sus artículos tienen una
coincidencia única.

Los descuentos se distribuyen como una plantilla portable y no como fixtures de
configuración. Por ello, ejecutar `bench migrate` no reemplaza la empresa, los
almacenes ni los ajustes locales de cada sitio.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/edinova
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### Quality checks

The following GitHub Actions workflow is configured:

- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
