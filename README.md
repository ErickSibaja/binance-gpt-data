# Binance GPT — recolector público, sin claves

Sistema de **solo lectura** para BTCUSDT/ETHUSDT, Spot y Futuros USDⓈ-M, en 15m/1h/4h. Recolecta hasta 500 velas por combinación, omite velas abiertas, valida OHLCV y calcula EMA 9/21/200 (SMA semilla), RSI 14 (Wilder), volumen base y razón contra promedio de 20 velas.

**No inicia sesión, no lee tu cuenta, no abre/cierra órdenes ni garantiza ingresos.** No expongas claves ni saldos privados en GitHub. Los archivos publicados son públicos. Los indicadores se calculan sobre cierres, NO son cotizaciones instantáneas. Si alguna de las 12 series falla, la salida queda `complete: false`, el job falla y no despliega una publicación parcial nueva. En tal caso el último sitio publicado podría estar desactualizado: revisar `generated_at_utc` y la ejecución en Actions.

## Configuración desde iPhone (sin computadora)

1. Crea cuenta o inicia sesión en [github.com](https://github.com). En Safari, activa **Solicitar sitio web de escritorio** cuando necesites ver todos los controles.
2. Crea un repositorio **público** llamado `binance-gpt-data`. No incluyas información personal ni API keys. Alternativamente, repositorio privado con Pages solo si tu plan lo permite.
3. Descomprime este ZIP desde Archivos de iOS. Sube `collector.py`, `README.md` y `tests/test_collector.py` al repositorio (GitHub → Add file → Upload files). Debe conservarse el directorio `tests`.
4. El archivo oculto `.github/workflows/market-data.yml` puede ser complicado de subir desde iOS. Método sencillo: en GitHub elige **Add file → Create new file**, escribe el nombre completo `.github/workflows/market-data.yml`, pega su contenido desde el archivo del ZIP (abrir con un editor de texto) y confirma el commit.
5. En **Settings → Pages → Build and deployment → Source**, elige **GitHub Actions**.
6. En **Actions → Binance GPT - datos diarios → Run workflow**, ejecútalo manualmente. Comprueba que los pasos de pruebas, consulta y despliegue terminen correctamente.
7. Tu página normalmente quedará en `https://TU_USUARIO.github.io/binance-gpt-data/` y el JSON en `https://TU_USUARIO.github.io/binance-gpt-data/latest.json`. Sustituye TU_USUARIO por tu usuario real de GitHub.
8. Comprueba en `latest.json` que `complete` sea `true`, haya 12 `results`, 0 `errors` y `generated_at_utc` sea reciente. Si GitHub Actions no logra alcanzar Binance, **no** es seguro inferir datos.

Programación: **6:40 a. m., zona America/Costa_Rica**, para intentar disponer de datos antes del informe ChatGPT de las 7:00 a. m. Los eventos programados pueden ejecutarse con retraso; si el recolector llega tarde, el informe NO debe usar datos anteriores como actuales. GitHub puede deshabilitar temporalmente cron en repositorios inactivos. Configura notificaciones de fallos de GitHub Actions.

## Conexión con ChatGPT

El despliegue de una URL pública **no hace que una automatización ChatGPT lea esa URL por sí sola**. Se requiere una prueba de acceso real a la URL desde el entorno del agente y confirmar que la tarea programada dispone de esa capacidad. Si no puede abrir la URL, usa un flujo de entrega compatible o comparte manualmente `latest.json` en el proyecto. **Hasta validar ese paso, no uses el informe de las 7:00 para señales de entrada.**

Prompt de prueba en ChatGPT:

> Abre esta URL pública sin inventar resultados: [PEGAR URL COMPLETA /latest.json]. Confirma generated_at_utc, complete, número de results/errors y muestra la EMA9, EMA21, EMA200 y RSI14 de ETHUSDT futures_usdm 1h. Si no tienes acceso efectivo a la respuesta JSON o está desactualizada, dilo y NO propongas operaciones.

## Fuentes oficiales

- Spot: https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
- Futuros: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data

## Limitaciones

No registra funding, spread, profundidad, horarios de noticias, precio mark, costo de liquidación, órdenes, saldo, comisiones del usuario, ni ejecución de stops. No calcula soporte/resistencia ni valida rentabilidad. EMA y RSI dependen del método de inicialización; pueden variar ligeramente de Binance. Todas las horas internas son UTC.
