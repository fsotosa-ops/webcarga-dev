import asyncio
import csv
import json
import os
import logging
import time
from datetime import date
from urllib.parse import urljoin
from playwright.async_api import (
    Page,
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from config import settings
from schemas import ExtractionArtifact

logger = logging.getLogger(__name__)


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class WingSuiteExtractor:
    SOURCE_NAME = "wingsuite"
    PRODUCT_NAME = "viajes-transportista"

    async def extract(
        self, client_name: str, date_from: date, date_to: date, timeout_ms: int
    ) -> ExtractionArtifact:
        ts = int(time.time())
        downloads_dir = os.path.join(os.getcwd(), "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        logger.info(f"Iniciando extracción WingSuite: {date_from} a {date_to}")

        async with async_playwright() as p:
            # Firefox porque Chromium 1091 headed falla en macOS 15 (crash en new_page).
            # Firefox además basta para el reCAPTCHA v3 invisible del login.
            browser = await p.firefox.launch(headless=settings.BROWSER_HEADLESS)
            context = await browser.new_context(
                accept_downloads=True,
                ignore_https_errors=True,
                viewport={"width": 1366, "height": 768},
            )
            page = await context.new_page()

            page.on("console", lambda msg: logger.debug(f"[console] {msg.type}: {msg.text}"))
            page.on("pageerror", lambda exc: logger.error(f"[pageerror] {exc}"))

            try:
                # generar_sesion.php abre un popup con la sesión iniciada y cierra la
                # pestaña original. Capturamos esa nueva página y continuamos en ella.
                page = await self._login(page, context, timeout_ms)
                await self._navigate_to_logistics_module(page, timeout_ms)
                await self._open_report(page, timeout_ms)

                local_path = await self._apply_filters_and_download(
                    page, client_name, date_from, date_to, downloads_dir, ts, timeout_ms
                )

                return ExtractionArtifact(
                    local_path=local_path,
                    source=self.SOURCE_NAME,
                    product=self.PRODUCT_NAME,
                    client_name=client_name,
                )
            except Exception as e:
                await self._dump_source(page, "error_wingsuite")
                logger.error(f"Error en la extracción: {e}")
                raise
            finally:
                await browser.close()

    async def _login(self, page: Page, context, timeout_ms: int) -> Page:
        logger.info("[STEP] Login...")
        await page.goto(settings.WINGSUITE_URL, timeout=timeout_ms)
        await page.wait_for_selector("#username", state="visible", timeout=timeout_ms)

        await page.fill("#username", settings.WINGSUITE_USER)
        await page.fill("#password", settings.WINGSUITE_PASS)
        await page.locator("#password").press("Enter")

        # Firefox navega en la misma pestaña; Chromium abre un popup y cierra la
        # original. Aceptamos cualquiera de los dos: polleamos hasta encontrar
        # una página viva con #side-menu.
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            for candidate in list(context.pages):
                if candidate.is_closed():
                    continue
                try:
                    if await candidate.locator("#side-menu").count() > 0:
                        logger.info(f"Login exitoso en {candidate.url}")
                        candidate.on("console", lambda m: logger.debug(f"[console] {m.type}: {m.text}"))
                        candidate.on("pageerror", lambda exc: logger.error(f"[pageerror] {exc}"))
                        return candidate
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        try:
            live_page = next((p for p in context.pages if not p.is_closed()), page)
            await self._dump_source(live_page, "error_login_wingsuite")
        except Exception:
            pass
        raise RuntimeError(
            "El login falló (posible rechazo reCAPTCHA o credenciales inválidas). "
            "Revisa docs/claude_logs/error_login_wingsuite.png"
        )

    async def _navigate_to_logistics_module(self, page: Page, timeout_ms: int):
        logger.info("[STEP] Entrando al Módulo Operación Logística...")
        # Post-login el usuario aterriza en /web/core/index.php; reutilizamos ese dir.
        target_url = urljoin(settings.WINGSUITE_URL, "index.php?id_app=5")
        await page.goto(target_url, timeout=timeout_ms)
        await page.wait_for_selector("#side-menu", state="visible", timeout=timeout_ms)

    async def _open_report(self, page: Page, timeout_ms: int):
        logger.info("[STEP] Abriendo reporte 4134 (Viajes por Transportista)...")
        # id_app=5 ya lanza cargarPaginaBd(5,4134) en el onload (codigo_fuente3.html:123),
        # pero forzamos por si el onload no corrió aún.
        await page.evaluate("funcionesTema.cargarPaginaBd('5','4134')")
        await page.wait_for_selector("#page-content", state="visible", timeout=timeout_ms)
        # Esperar a que el contenido del reporte pinte dentro de #page-content
        await page.wait_for_function(
            "document.querySelector('#page-content').innerText.trim().length > 0",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(1500)
        await self._dump_source(page, "codigo_fuente4_reporte")

    async def _apply_filters_and_download(
        self,
        page: Page,
        client_name: str,
        date_from: date,
        date_to: date,
        downloads_dir: str,
        ts: int,
        timeout_ms: int,
    ) -> str:
        logger.info("[STEP] Aplicando filtros y descargando CSV...")

        # Por PoC respetamos el rango por defecto que el sitio pinta en
        # fecha_inicio/fecha_fin (día 1 del mes en curso → último día del mes).
        # Si se sobrescriben los inputs por fuera, bootstrap-datetimepicker a
        # veces deja el form sin disparar el XHR al llamar buscar_listado().
        # Los parámetros date_from/date_to quedan disponibles en el request
        # para futuras iteraciones que sí los apliquen.
        await page.wait_for_selector("#fecha_inicio", state="visible", timeout=timeout_ms)
        default_range = await page.evaluate(
            "() => ({ fi: document.querySelector('#fecha_inicio').value, "
            "ff: document.querySelector('#fecha_fin').value })"
        )
        logger.info(
            f"Rango por defecto del sitio: {default_range['fi']} → {default_range['ff']}"
        )

        # Capturamos el JSON del endpoint y escribimos el CSV nosotros mismos.
        # Los botones CSV/Excel del DataTables del sitio no disparan descarga
        # (sólo copiar y dinámica funcionan); el XHR ya trae toda la data.
        async with page.expect_response(
            lambda r: "viajes.obtener_completo_transportista" in r.url,
            timeout=timeout_ms,
        ) as resp_info:
            await page.evaluate("buscar_listado()")

        response = await resp_info.value
        payload = await response.json()
        rows = self._extract_rows(payload)
        logger.info(f"Filas recibidas: {len(rows)}")

        filename = f"wingsuite_poc_{client_name}_{ts}.csv"
        local_file_path = os.path.join(downloads_dir, filename)
        self._write_csv(local_file_path, rows)
        logger.info(f"CSV generado: {local_file_path}")
        return local_file_path

    @staticmethod
    def _extract_rows(payload) -> list[dict]:
        # El endpoint `viajes.obtener_completo_transportista` envuelve la data
        # en {"status": ..., "resp": [...]}. Aceptamos también lista suelta por
        # robustez.
        if isinstance(payload, dict):
            data = payload.get("resp")
        elif isinstance(payload, list):
            data = payload
        else:
            data = None
        if not isinstance(data, list):
            return []
        return [r for r in data if isinstance(r, dict)]

    @staticmethod
    def _write_csv(path: str, rows: list[dict]) -> None:
        # Cabecera = unión de claves en orden de aparición para ser estables si
        # algunos registros tienen campos opcionales. Separador ';' igual que el
        # botón CSV del sitio (DataTables: fieldSeparator=';').
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for k in row.keys():
                if k not in seen:
                    seen.add(k)
                    fieldnames.append(k)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _stringify(row.get(k)) for k in fieldnames})

    @staticmethod
    async def _dump_source(page: Page, label: str) -> None:
        """Guarda HTML y screenshot del estado actual para diagnóstico."""
        try:
            html_dir = os.path.join(os.getcwd(), "codigo_fuente")
            shot_dir = os.path.join(os.getcwd(), "docs", "claude_logs")
            os.makedirs(html_dir, exist_ok=True)
            os.makedirs(shot_dir, exist_ok=True)
            html = await page.content()
            with open(os.path.join(html_dir, f"{label}.html"), "w", encoding="utf-8") as f:
                f.write(html)
            await page.screenshot(path=os.path.join(shot_dir, f"{label}.png"), full_page=True)
            logger.info(f"[dump] {label}.html + {label}.png")
        except Exception as e:
            logger.warning(f"No se pudo dump_source({label}): {e}")
