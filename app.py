"""
Vercel Serverless WSGI Entry Point for Anti-Ragging Portal
Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College
NBA - Tier 1 SAR Guideline: Criterion No. 10.1.4

Exposes the WSGI application callable 'app' for Vercel deployment.
Suppresses persistent background scheduler threads during serverless execution.
"""
from backend import create_app

# Instantiate application once for WSGI invocation
app = create_app(config_overrides={"DISABLE_REMINDER_SCHEDULER": True})

if __name__ == "__main__":
    app.run()

