
from shared.core.Logger import get_logger

logger = get_logger(__name__)

from shared.core.CacheUtils import fm_cache

async def get_breadcrumb(service, folder_stack, current_folder, get_folder_name_func, provider_name=""):
   path = []
   for folder_id in folder_stack:
       if folder_id != 'root':
           cache_key = f"folder_name_{provider_name}_{folder_id}"
           name = fm_cache.get(cache_key)
           if not name:
               try:
                   name = await get_folder_name_func(service, folder_id)
                   fm_cache.set(cache_key, name, ttl=300)
               except Exception as e:
                   logger.error(f"Error in get_breadcrumb: failed to get folder name for {folder_id}: {e}")
                   name = '...'
           path.append(name)
   if current_folder != 'root':
       cache_key = f"folder_name_{provider_name}_{current_folder}"
       name = fm_cache.get(cache_key)
       if not name:
           try:
               name = await get_folder_name_func(service, current_folder)
               fm_cache.set(cache_key, name, ttl=300)
           except Exception as e:
               logger.error(f"Error in get_breadcrumb: failed to get folder name for {current_folder}: {e}")
               name = '...'
       path.append(name)
   
   if not path:
       return "/"
   return "/" + "/".join(path)


