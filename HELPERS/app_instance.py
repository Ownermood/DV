# Global app instance
# This module provides a global app instance that can be imported by other modules


app = None

def set_app(app_instance):
    """Set the global app instance"""
    global app
    app = app_instance

def get_app():
    """Get the global app instance"""
    return app
