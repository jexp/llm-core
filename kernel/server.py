"""ASGI entry point: uvicorn kernel.server:app"""

from kernel.gateway import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("kernel.server:app", host="0.0.0.0", port=8000, reload=False)
