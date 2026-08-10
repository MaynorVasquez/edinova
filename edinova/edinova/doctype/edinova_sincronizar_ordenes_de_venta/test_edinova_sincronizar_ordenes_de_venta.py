# Copyright (c) 2025, Maynor Vasquez and Contributors
# See license.txt

from unittest.mock import Mock, call, patch

from frappe.tests.utils import FrappeTestCase

from edinova.api.erpnext_orden_venta import (
    EdinovaSyncError,
    _cargar_items_por_ean,
    _existe_orden_para_gln,
    _normalizar_orden,
    calcular_precio_negociado,
    validar_cantidades,
)
from edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta.edinova_sincronizar_ordenes_de_venta import (
    LOCK_KEY,
    LOCK_OWNER_KEY,
    LOCK_TTL_SEC,
    _adquirir_lock,
    _liberar_lock,
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

    def test_redondea_a_seis_decimales(self):
        descuentos = {"ITM-1": {"descuento": 4.25, "iva": 12}}
        self.assertEqual(
            calcular_precio_negociado("ITM-1", 10.1234567, descuentos),
            10.856395,
        )

    def test_rechaza_precio_negativo(self):
        with self.assertRaisesRegex(EdinovaSyncError, "no puede ser negativo"):
            calcular_precio_negociado("ITM-1", -1, {})

    def test_rechaza_porcentajes_fuera_de_rango(self):
        descuentos = {"ITM-1": {"descuento": 101, "iva": 12}}
        with self.assertRaisesRegex(EdinovaSyncError, "entre 0 y 100"):
            calcular_precio_negociado("ITM-1", 100, descuentos)


class TestNormalizarOrden(FrappeTestCase):
    def build_order(self, quantity):
        return {
            "purchaseOrderNumber": {"po": "PO-1", "datePO": "202608101200"},
            "items": [
                {
                    "ean": "111",
                    "intCode": "A1",
                    "quantity": quantity,
                    "unitPrice": 10,
                }
            ],
            "merchandisedistribution": [
                {"gln": "GLN-1", "items": [{"ean": "111", "quantity": quantity}]}
            ],
        }

    def test_accepts_integer_quantity_as_string(self):
        order = _normalizar_orden(self.build_order("10"), 0)

        self.assertEqual(order["items"][0]["quantity"], 10)
        self.assertEqual(
            order["merchandisedistribution"][0]["items"][0]["quantity"], 10
        )

    def test_rejects_fractional_quantity_instead_of_truncating(self):
        with self.assertRaisesRegex(EdinovaSyncError, "debe ser un entero"):
            _normalizar_orden(self.build_order(1.8), 0)

    def test_rejects_zero_quantity(self):
        with self.assertRaisesRegex(EdinovaSyncError, "mayor que cero"):
            _normalizar_orden(self.build_order(0), 0)

    def test_rejects_negative_quantity(self):
        with self.assertRaisesRegex(EdinovaSyncError, "mayor que cero"):
            _normalizar_orden(self.build_order(-2), 0)

    def test_rejects_duplicate_ean_in_order_items(self):
        order = self.build_order(10)
        order["items"].append({**order["items"][0]})

        with self.assertRaisesRegex(EdinovaSyncError, "EAN duplicado"):
            _normalizar_orden(order, 0)

    def test_rejects_duplicate_ean_inside_same_gln(self):
        order = self.build_order(10)
        distribution_items = order["merchandisedistribution"][0]["items"]
        distribution_items.append({**distribution_items[0]})

        with self.assertRaisesRegex(EdinovaSyncError, "dentro del mismo GLN"):
            _normalizar_orden(order, 0)

    def test_allows_same_ean_in_different_glns(self):
        order = self.build_order(10)
        order["merchandisedistribution"][0]["items"][0]["quantity"] = 4
        order["merchandisedistribution"].append(
            {"gln": "GLN-2", "items": [{"ean": "111", "quantity": 6}]}
        )

        normalized = _normalizar_orden(order, 0)

        self.assertEqual(len(normalized["merchandisedistribution"]), 2)


class TestCargarItemsPorEAN(FrappeTestCase):
    @patch("edinova.api.erpnext_orden_venta.frappe.get_all")
    def test_returns_unique_active_item(self, get_all):
        get_all.return_value = [
            {"name": "ITM-1", "custom_ean": "111", "disabled": 0}
        ]

        result = _cargar_items_por_ean(["111"])

        self.assertEqual(result, {"111": "ITM-1"})
        get_all.assert_called_once_with(
            "Item",
            filters={"custom_ean": ["in", ["111"]], "disabled": 0},
            fields=["name", "custom_ean", "disabled"],
        )

    @patch("edinova.api.erpnext_orden_venta.frappe.get_all")
    def test_rejects_ean_assigned_to_multiple_active_items(self, get_all):
        get_all.return_value = [
            {"name": "ITM-1", "custom_ean": "111", "disabled": 0},
            {"name": "ITM-2", "custom_ean": "111", "disabled": 0},
        ]

        with self.assertRaisesRegex(EdinovaSyncError, "ITM-1, ITM-2"):
            _cargar_items_por_ean(["111"])


class TestIdempotenciaPorGLN(FrappeTestCase):
    @patch("edinova.api.erpnext_orden_venta.frappe.db.exists")
    def test_same_po_allows_different_glns(self, exists):
        exists.side_effect = [True, False]

        self.assertTrue(_existe_orden_para_gln("PO-1", "GLN-1", "CLI00001"))
        self.assertFalse(_existe_orden_para_gln("PO-1", "GLN-2", "CLI00001"))

        self.assertEqual(
            exists.call_args_list,
            [
                call(
                    "Sales Order",
                    {
                        "po_no": "PO-1",
                        "custom_gln": "GLN-1",
                        "customer": "CLI00001",
                    },
                ),
                call(
                    "Sales Order",
                    {
                        "po_no": "PO-1",
                        "custom_gln": "GLN-2",
                        "customer": "CLI00001",
                    },
                ),
            ],
        )


class TestSyncLock(FrappeTestCase):
    @patch(
        "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
        "edinova_sincronizar_ordenes_de_venta._sweep_stale_syncs"
    )
    @patch(
        "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
        "edinova_sincronizar_ordenes_de_venta.frappe.cache"
    )
    def test_acquires_redis_lock_atomically(self, get_cache, sweep):
        cache = Mock()
        lock = Mock()
        lock.acquire.return_value = True
        cache.make_key.return_value = b"site|edinova:sync_lock"
        cache.lock.return_value = lock
        get_cache.return_value = cache

        acquired_lock, owner = _adquirir_lock("SYNC-1")

        self.assertIs(acquired_lock, lock)
        self.assertIsNone(owner)
        sweep.assert_called_once_with(exclude="SYNC-1")
        cache.lock.assert_called_once_with(
            cache.make_key.return_value,
            timeout=LOCK_TTL_SEC,
            blocking=False,
        )
        lock.acquire.assert_called_once_with(blocking=False)
        cache.set_value.assert_called_once_with(
            LOCK_OWNER_KEY, "SYNC-1", expires_in_sec=LOCK_TTL_SEC
        )

    @patch(
        "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
        "edinova_sincronizar_ordenes_de_venta._sweep_stale_syncs"
    )
    @patch(
        "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
        "edinova_sincronizar_ordenes_de_venta.frappe.cache"
    )
    def test_reports_owner_when_lock_is_busy(self, get_cache, _sweep):
        cache = Mock()
        lock = Mock()
        lock.acquire.return_value = False
        cache.lock.return_value = lock
        cache.get_value.return_value = "SYNC-ACTIVE"
        get_cache.return_value = cache

        acquired_lock, owner = _adquirir_lock("SYNC-2")

        self.assertIsNone(acquired_lock)
        self.assertEqual(owner, "SYNC-ACTIVE")
        cache.get_value.assert_called_once_with(LOCK_OWNER_KEY, expires=True)

    @patch(
        "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
        "edinova_sincronizar_ordenes_de_venta.frappe.cache"
    )
    def test_releases_only_its_own_owner_marker(self, get_cache):
        cache = Mock()
        cache.get_value.return_value = "SYNC-1"
        get_cache.return_value = cache
        lock = Mock()

        _liberar_lock(lock, "SYNC-1")

        lock.release.assert_called_once_with()
        cache.delete_value.assert_called_once_with(LOCK_OWNER_KEY)


class TestEdinovaSincronizarOrdenesdeVenta(FrappeTestCase):
    pass
