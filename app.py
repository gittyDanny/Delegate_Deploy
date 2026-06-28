from flask import Flask

from config import INSTANCE_FOLDER, MAX_CONTENT_LENGTH, SECRET_KEY, UPLOAD_FOLDER
from models.database import init_db
from routes.merchant_routes import merchant_bp
from routes.web_routes import web_bp


def create_app() -> Flask:
    app = Flask(__name__)

    UPLOAD_FOLDER.mkdir(exist_ok=True)
    INSTANCE_FOLDER.mkdir(exist_ok=True)

    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.secret_key = SECRET_KEY

    init_db()

    app.register_blueprint(web_bp)
    app.register_blueprint(merchant_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)