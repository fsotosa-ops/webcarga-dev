import logging
from fastapi import FastAPI
from schemas import ExtractionRequest
from wingsuite_scraper import WingSuiteExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="PoC WingSuite Extractor")

@app.post("/test-wingsuite")
async def test_wingsuite(request: ExtractionRequest):
    extractor = WingSuiteExtractor()
    
    artifact = await extractor.extract(
        client_name=request.client_name,
        date_from=request.date_from,
        date_to=request.date_to,
        timeout_ms=request.timeout_ms
    )
    
    return {"status": "success", "artifact": artifact}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)