import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # solo para desarrollo; en producción usa servir.py. El modo depuración requiere FLASK_DEBUG=1
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes", "si"))
