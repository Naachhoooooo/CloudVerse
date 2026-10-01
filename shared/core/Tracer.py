import uuid
from telegram import Update
from telegram.ext import TypeHandler, ContextTypes
from shared.core.Logger import trace_id_var, telegram_id_var, username_var

async def set_trace_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Middleware to set a unique trace_id and user identifiers for every incoming update."""
    trace_id = uuid.uuid4().hex[:8]
    trace_id_var.set(trace_id)
    
    if update.effective_user:
        telegram_id_var.set(update.effective_user.id)
        username_var.set(update.effective_user.username)
        
    # Return None so the update propagates to other handlers
    return None

def get_trace_middleware():
    """Returns a TypeHandler that catches all updates to inject trace_id."""
    return TypeHandler(Update, set_trace_id)
