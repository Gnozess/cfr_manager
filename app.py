from flask import Flask
from config import Config
from extensions import db, login_manager
import os

# Charger .env en local uniquement
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def create_app():
    app = Flask(__name__)

    # Correction URL PostgreSQL (Render)
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
            try:
                from models.abonnement import Abonnement
                from datetime import date

                alertes_count = Abonnement.query.filter(
                    Abonnement.statut == 'actif',
                    Abonnement.date_fin >= date.today(),
                    Abonnement.date_fin <= date.fromordinal(
                        date.today().toordinal() + 7)
                ).count()
            except:
                alertes_count = 0

        return {
            'alertes_count': alertes_count,
            'current_user': current_user
        }

    # Routes
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

    # Init DB
    with app.app_context():
        db.create_all()

        try:
            # Créer l'admin par défaut
            if not Admin.query.filter_by(email='admin@gmail.com').first():
                admin = Admin(
                    nom='Administrateur',
                    email='admin@gmail.com',
                    role='admin',
                    actif=True
                )
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print('✅ Admin cree')

            # Initialiser les prix journaliers
            from models.config import Config as GymConfig
            if not GymConfig.query.filter_by(cle='journalier_simple').first():
                db.session.add(GymConfig(
                    cle='journalier_simple',
                    valeur=2000,
                    description='Prix journalier salle ou fitness'
                ))
            if not GymConfig.query.filter_by(cle='journalier_complet').first():
                db.session.add(GymConfig(
                    cle='journalier_complet',
                    valeur=3000,
                    description='Prix journalier salle + fitness'
                ))
            db.session.commit()
            print('✅ Configuration initialisee')

        except Exception as e:
            print(f'Erreur initialisation : {e}')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)