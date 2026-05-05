from flask import Flask
from config import Config
from extensions import db, login_manager
import os

def create_app():
    app = Flask(__name__)

    # Corriger l'URL PostgreSQL de :contentReference[oaicite:0]{index=0}
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
        os.environ['DATABASE_URL'] = db_url

    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    from models.admin import Admin

    @login_manager.user_loader
    def load_user(user_id):
        return Admin.query.get(int(user_id))

    @app.context_processor
    def inject_context():
        from flask_login import current_user
        alertes_count = 0
        if current_user.is_authenticated:
            from models.abonnement import Abonnement
            from datetime import date
            alertes_count = Abonnement.query.filter(
                Abonnement.statut == 'actif',
                Abonnement.date_fin >= date.today(),
                Abonnement.date_fin <= date.fromordinal(
                    date.today().toordinal() + 7)
            ).count()
        return {
            'alertes_count': alertes_count,
            'current_user': current_user
        }

    # Import routes
    from routes.auth import auth_bp
    from routes.membres import membres_bp
    from routes.abonnements import abonnements_bp
    from routes.forfaits import forfaits_bp
    from routes.caisse import caisse_bp
    from routes.utilisateurs import utilisateurs_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(membres_bp)
    app.register_blueprint(abonnements_bp)
    app.register_blueprint(forfaits_bp)
    app.register_blueprint(caisse_bp)
    app.register_blueprint(utilisateurs_bp)

    # 🔥 INIT DB + ADMIN COMPATIBLE AVEC TON LOGIN
    from werkzeug.security import generate_password_hash

    with app.app_context():
        db.create_all()

        # Création admin si inexistant
        if not Admin.query.filter_by(email="admin@gmail.com").first():
            admin = Admin(
                email="admin@gmail.com",
                nom="Admin",
                actif=True,
                est_membre=False,
                password=generate_password_hash("admin123")
            )
            db.session.add(admin)
            db.session.commit()

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=False)