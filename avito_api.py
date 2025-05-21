import aiohttp
import asyncio
import logging
from typing import Dict, Optional, List, Any
from datetime import datetime, timedelta
from config import (
    AVITO_API_BASE_URL,
    AVITO_AUTH_URL,
    AVITO_CLIENT_ID,
    AVITO_CLIENT_SECRET,
    AVITO_ACCESS_TOKEN,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_DELAY
)

logger = logging.getLogger(__name__)

class AvitoAPI:
    def __init__(self):
        self.base_url = AVITO_API_BASE_URL
        self.access_token = AVITO_ACCESS_TOKEN
        self.client_id = AVITO_CLIENT_ID
        self.client_secret = AVITO_CLIENT_SECRET
        self.token_expires_at = None
        self._session = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Получить или создать сессию"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            )
        return self._session

    def _get_headers(self) -> Dict[str, str]:
        """Получить заголовки для запросов"""
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        if self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'
        return headers

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        retry_count: int = 0
    ) -> Dict:
        """Выполнить запрос к API с обработкой ошибок и повторными попытками"""
        if retry_count >= MAX_RETRIES:
            raise Exception(f"Превышено максимальное количество попыток для {endpoint}")

        session = await self.get_session()
        url = f"{self.base_url}{endpoint}"

        try:
            async with session.request(method, url, params=params, json=data) as response:
                response_text = await response.text()
                logger.debug(f"Response from {url}: {response_text}")

                if response.status == 401:
                    await self.refresh_token()
                    return await self._make_request(method, endpoint, params, data, retry_count + 1)
                
                if response.status >= 500:
                    await asyncio.sleep(RETRY_DELAY)
                    return await self._make_request(method, endpoint, params, data, retry_count + 1)

                if response.status >= 400:
                    error_data = await response.json()
                    raise Exception(f"API error: {error_data}")

                return await response.json()

        except aiohttp.ClientError as e:
            logger.error(f"Request error for {url}: {str(e)}")
            await asyncio.sleep(RETRY_DELAY)
            return await self._make_request(method, endpoint, params, data, retry_count + 1)

    async def refresh_token(self) -> None:
        """Обновить токен доступа"""
        async with aiohttp.ClientSession() as session:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }
            async with session.post(AVITO_AUTH_URL, json=data) as response:
                if response.status == 200:
                    token_data = await response.json()
                    self.access_token = token_data['access_token']
                    expires_in = token_data.get('expires_in', 3600)
                    self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                else:
                    raise Exception("Failed to refresh token")

    async def search_items(
        self,
        category_id: Optional[int] = None,
        location_id: Optional[int] = None,
        search_query: Optional[str] = None,
        price_from: Optional[int] = None,
        price_to: Optional[int] = None,
        sort_by: str = "date",
        page: int = 1,
        per_page: int = 50
    ) -> Dict:
        """Поиск объявлений по параметрам"""
        params = {
            'page': page,
            'per_page': per_page,
            'sort_by': sort_by
        }
        
        if category_id:
            params['category_id'] = category_id
        if location_id:
            params['location_id'] = location_id
        if search_query:
            params['query'] = search_query
        if price_from:
            params['price_from'] = price_from
        if price_to:
            params['price_to'] = price_to

        return await self._make_request('GET', '/items', params=params)

    async def get_item_details(self, item_id: str) -> Dict:
        """Получить детальную информацию об объявлении"""
        return await self._make_request('GET', f'/items/{item_id}')

    async def get_categories(self) -> List[Dict]:
        """Получить список категорий"""
        return await self._make_request('GET', '/categories')

    async def get_locations(self, query: str) -> List[Dict]:
        """Поиск локаций по запросу"""
        params = {'query': query}
        return await self._make_request('GET', '/locations', params=params)

    async def get_item_stats(self, item_id: str) -> Dict:
        """Получить статистику по объявлению"""
        return await self._make_request('GET', f'/items/{item_id}/stats')

    async def close(self) -> None:
        """Закрыть сессию"""
        if self._session and not self._session.closed:
            await self._session.close() 