# Copyright (c) 2025, Maynor Vasquez and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from edinova.api.erpnext_orden_venta import (
    calcular_precio_negociado,
    validar_cantidades,
)


class TestValidarCantidades(FrappeTestCase):
    def test_match_cuando_distribuido_iguala_esperado(self):
        ordenes = [{
            "purchaseOrderNumber": {"po": "PO-1"},
            "items": [{"ean": "111", "intCode": "A1", "quantity": 10}],
            "merchandisedistribution": [{
                "items": [{"ean": "111", "quantity": 10}],
            }],
        }]

        result = validar_cantidades(ordenes)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["po"], "PO-1")
        item = result[0]["items"][0]
        self.assertTrue(item["match"])
        self.assertEqual(item["difference"], 0)

    def test_diferencia_cuando_distribuido_es_menor(self):
        ordenes = [{
            "purchaseOrderNumber": {"po": "PO-2"},
            "items": [{"ean": "222", "intCode": "B1", "quantity": 10}],
            "merchandisedistribution": [{
                "items": [{"ean": "222", "quantity": 7}],
            }],
        }]

        item = validar_cantidades(ordenes)[0]["items"][0]

        self.assertFalse(item["match"])
        self.assertEqual(item["expectedQuantity"], 10)
        self.assertEqual(item["distributedQuantity"], 7)
        self.assertEqual(item["difference"], 3)

    def test_distribucion_en_multiples_glns_se_suma(self):
        ordenes = [{
            "purchaseOrderNumber": {"po": "PO-3"},
            "items": [{"ean": "333", "intCode": "C1", "quantity": 10}],
            "merchandisedistribution": [
                {"items": [{"ean": "333", "quantity": 4}]},
                {"items": [{"ean": "333", "quantity": 6}]},
            ],
        }]

        item = validar_cantidades(ordenes)[0]["items"][0]

        self.assertTrue(item["match"])
        self.assertEqual(item["distributedQuantity"], 10)


class TestCalcularPrecioNegociado(FrappeTestCase):
    def test_sin_iva_ni_descuento(self):
        descuentos = {"ITM-1": {"descuento": None, "iva": None}}
        self.assertEqual(calcular_precio_negociado("ITM-1", 100, descuentos), 100)

    def test_aplica_iva(self):
        descuentos = {"ITM-1": {"descuento": None, "iva": 12}}
        self.assertEqual(calcular_precio_negociado("ITM-1", 100, descuentos), 112)

    def test_aplica_iva_y_descuento(self):
        descuentos = {"ITM-1": {"descuento": 10, "iva": 12}}
        # 100 * 1.12 * 0.9 = 100.8
        self.assertAlmostEqual(
            calcular_precio_negociado("ITM-1", 100, descuentos), 100.8
        )

    def test_item_sin_configuracion_se_trata_como_sin_iva_ni_descuento(self):
        self.assertEqual(calcular_precio_negociado("INEXISTENTE", 50, {}), 50)


class TestEdinovaSincronizarOrdenesdeVenta(FrappeTestCase):
    pass
