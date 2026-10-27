"""Entry point kept from the first version:  uvicorn api:app --reload"""

from pricepulse.api.app import create_app

app = create_app()
