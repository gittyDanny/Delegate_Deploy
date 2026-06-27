from flask import Flask

from config import UPLOAD_FOLDER, MAX_CONTENT_LENGTH
from routes.web_routes import web_bp


def create_app() -> Flask:
    app = Flask(__name__)

    UPLOAD_FOLDER.mkdir(exist_ok=True)

    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    app.register_blueprint(web_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)