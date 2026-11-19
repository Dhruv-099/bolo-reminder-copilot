# notifier/apps.py
from django.apps import AppConfig
import os
import logging

logger = logging.getLogger(__name__)

class NotifierConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifier'

    def ready(self):
        if os.environ.get('RUN_MAIN') == 'true' and not os.environ.get('DJANGO_SCHEDULER_STARTED'):
            logger.info("Attempting to start APScheduler for development...")
            from apscheduler.schedulers.background import BackgroundScheduler
            from django.core import management
            
            def send_notifications_job():
                logger.info("APScheduler: Triggering 'send_notifications' job...")
                try:
                    management.call_command('send_notifications')
                except Exception as e:
                    logger.error(f"Error in APScheduler job: {e}", exc_info=True)

            scheduler = BackgroundScheduler(timezone="Asia/Kolkata") 
            scheduler.add_job(send_notifications_job, 'interval', minutes=1, id='send_notifications_job', replace_existing=True)
            
            try:
                scheduler.start()
                logger.info("APScheduler started successfully for development.")
                os.environ['DJANGO_SCHEDULER_STARTED'] = 'true' # Mark as started
            except Exception as e:
                logger.error(f"APScheduler could not start: {e}", exc_info=True)
        elif os.environ.get('DJANGO_SCHEDULER_STARTED'):
            logger.info("APScheduler already marked as started.")
        else:
            logger.info("APScheduler not starting (not in main process or conditions not met).")

        # Load Hugging Face models once at startup if desired
        # from .services import hf_models_service
        # hf_models_service.load_hf_models()