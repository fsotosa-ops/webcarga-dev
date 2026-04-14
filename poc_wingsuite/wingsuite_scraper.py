import os
import logging
import time
from datetime import date
from playwright.async_api import (
    Page,
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from poc_wingsuite.config import settings
from schemas import ExtractionArtifact

logger = logging.getLogger(__name__)

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
            # Lanzamos Firefox (Headless depende del .env)
            browser = await p.firefox.launch(headless=settings.BROWSER_HEADLESS)
            context = await browser.new_context(
                accept_downloads=True, 
                ignore_https_errors=True,
                viewport={"width": 1366, "height": 768}
            )
            page = await context.new_page()

            page.on("console", lambda msg: logger.debug(f"[console] {msg.type}: {msg.text}"))
            page.on("pageerror", lambda exc: logger.error(f"[pageerror] {exc}"))

            try:
                await self._login(page, timeout_ms)
                await self._navigate_to_logistics_module(page, timeout_ms)
                await self._open_report(page, timeout_ms)
                
                local_path = await self._apply_filters_and_download(
                    page, client_name, date_from, date_to, downloads_dir, ts, timeout_ms
                )

                return ExtractionArtifact(
                    local_path=local_path,
                    source=self.SOURCE_NAME,
                    product=self.PRODUCT_NAME,
                    client_name=client_name
                )
            except Exception as e:
                await self._safe_screenshot(page, "error_poc_wingsuite")
                logger.error(f"Error en la extracción: {e}")
                raise
            finally:
                await browser.close()

    async def _login(self, page: Page, timeout_ms: int):
        logger.info("[STEP] Login...")
        await page.goto(settings.WINGSUITE_URL, timeout=timeout_ms)
        await page.wait_for_selector("#username", state="visible", timeout=timeout_ms)
        await page.fill("#username", settings.WINGSUITE_USER)
        await page.fill("#password", settings.WINGSUITE_PASS)
        
        async with page.expect_navigation(timeout=timeout_ms):
            await page.click("button.btn-login[type='submit']")

    async def _navigate_to_logistics_module(self, page: Page, timeout_ms: int):
        logger.info("[STEP] Módulo Operación Logística...")
        target_url = f"{settings.WINGSUITE_URL.rstrip('/')}/index.php?id_app=5"
        await page.goto(target_url, timeout=timeout_ms)
        await page.wait_for_selector("#side-menu", state="visible", timeout=timeout_ms)

    async def _open_report(self, page: Page, timeout_ms: int):
        logger.info("[STEP] Abriendo reporte 4134...")
        await page.evaluate("funcionesTema.cargarPaginaBd('5','4134')")
        await page.wait_for_selector("#page-content", state="visible", timeout=timeout_ms)
        await page.wait_for_timeout(2000) # Pausa táctica para que el DOM se asiente

    async def _apply_filters_and_download(
        self, page: Page, client_name: str, date_from: date, date_to: date, downloads_dir: str, ts: int, timeout_ms: int
    ) -> str:
        logger.info("[STEP] Buscando placeholders de descarga...")
        
        # ⚠️ IMPORTANTE: Inspecciona el navegador cuando se pause aquí y cambia estos IDs por los reales.
        SEL_DATE_FROM = "#reemplazar_id_fecha_desde" 
        SEL_DATE_TO = "#reemplazar_id_fecha_hasta"
        SEL_BTN_EXPORT = "#reemplazar_id_boton_exportar"

        from_str = date_from.strftime("%d-%m-%Y")
        to_str = date_to.strftime("%d-%m-%Y")

        try:
            # Si el código se cae aquí en tu primera prueba, es porque los selectores no existen.
            # Pon breakpoints o time.sleep(30) aquí para darte tiempo de inspeccionar el HTML del DOM real.
            
            # await page.fill(SEL_DATE_FROM, from_str)
            # await page.fill(SEL_DATE_TO, to_str)

            logger.info("Esperando click en botón de descarga...")
            async with page.expect_download(timeout=timeout_ms) as download_info:
                # await page.click(SEL_BTN_EXPORT)
                pass # <- Quita esto cuando tengas el botón real
            
            download = await download_info.value
            ext = os.path.splitext(download.suggested_filename)[1] or ".csv"
            
            # Nombre de archivo simplificado para la PoC
            filename = f"wingsuite_poc_{client_name}_{ts}{ext}"
            local_file_path = os.path.join(downloads_dir, filename)
            
            await download.save_as(local_file_path)
            logger.info(f"Descarga exitosa en: {local_file_path}")
            return local_file_path

        except Exception as e:
            raise RuntimeError(f"Fallo al intentar aplicar filtros/descargar: {e}")

    @staticmethod
    async def _safe_screenshot(page: Page, label: str) -> None:
        try:
            await page.screenshot(path=f"{label}.png")
        except Exception:
            pass