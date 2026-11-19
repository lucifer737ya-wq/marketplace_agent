"""Контрактные тесты этапа 2 без обращений к реальным маркетплейсам."""
import unittest
from unittest.mock import AsyncMock, patch

from tests import offline

offline.require_isolation()  # before importing application settings

from database.models import init_db, close_db
from database.crud import create_pending_action, get_pending_action, claim_price_action, finish_price_action
from services.common.price_auditor import calculate_ozon_unit_profit, evaluate_ozon_dual_scenarios, find_optimal_wb_price
from services.common.unit_economics import calculate_floor_price
from services.wb.statistics_api import WBStatisticsAPI
from services.ozon.seller_api import OzonSellerAPI
from bot.handlers.callbacks import _ozon_result, _resolve_wb_nm_id, cb_approve_action, cb_reject_action


class TestPrices(unittest.TestCase):
    def test_commission_boundary(self):
        self.assertEqual(calculate_ozon_unit_profit(300, 60, 39, 0, 3, .063, .22)['comm_pct'], 22)
        self.assertEqual(calculate_ozon_unit_profit(300.01, 60, 39, 0, 3, .063, .22)['comm_pct'], 52)
        d = evaluate_ozon_dual_scenarios(299, 60, .22, .23, 10, 10, 39, 10, 5, .063)
        self.assertGreater(d['tier_over300']['price'], 300)
        self.assertGreaterEqual(d['tier_over300']['net_profit'], max(d['tier_299']['net_profit'] + 25, 55))

    def test_invalid_economics(self):
        with self.assertRaises(ValueError): calculate_floor_price(1, 1, 1, 1, 1, .9, .1, .06, .1)
        with self.assertRaises(ValueError): find_optimal_wb_price(100, 10, .8, 1, 0, 10, 10, 1, .1)

    def test_identifiers_and_ozon_result(self):
        self.assertEqual(_resolve_wb_nm_id({'nm_id': 123}, '1001', 'act:app:42'), 123)
        with self.assertRaises(ValueError): _resolve_wb_nm_id({'external_id':'1001'}, '1001', 'act:app:42')
        _ozon_result({'result':[{'offer_id':'1001','updated':True,'errors':[]}]}, '1001', '')
        with self.assertRaises(ValueError): _ozon_result({'result':[{'offer_id':'1001','updated':False,'errors':[{'message':'bad'}]}]}, '1001', '')
        with self.assertRaises(RuntimeError): _ozon_result({'result':[]}, '1001', '')


class TestAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): await init_db()
    async def asyncTearDown(self): await close_db()

    async def test_action_group_atomic(self):
        import asyncio, uuid
        group = uuid.uuid4().hex
        a = await create_pending_action('ozon','1001','update_price', {'group_id':group})
        b = await create_pending_action('ozon','1001','update_price', {'group_id':group})
        results = await asyncio.gather(claim_price_action(a.id), claim_price_action(b.id))
        self.assertEqual(sum(x is not None for x in results), 1)
        winner = next(x for x in results if x is not None)
        await finish_price_action(winner.id, 'executed')
        self.assertEqual({(await get_pending_action(a.id)).status, (await get_pending_action(b.id)).status}, {'executed','rejected'})

    async def test_wb_submit(self):
        wb=WBStatisticsAPI()
        wb._request=AsyncMock(side_effect=[{'data':{'listGoods':[{'nmID':123,'discount':20}]}}, {'error':False,'data':{'id':12345}}])
        r=await wb.update_price_and_min_price(123, 299)
        self.assertEqual(r['status'],'submitted'); self.assertEqual(r['min_price_for_promotions'],275)
        self.assertFalse(r['min_price_set'])
        self.assertEqual(wb._request.await_args_list[1].kwargs['json_data']['data'][0], {'nmID':123,'price':374,'discount':20})
        wb._request=AsyncMock(side_effect=[{'data':{'listGoods':[{'nmID':123,'discount':20}]}}, {'error':True,'errorText':'bad'}])
        with self.assertRaises(ValueError): await wb.update_price_and_min_price(123, 299)

    async def test_wb_inventory(self):
        wb=WBStatisticsAPI(); wb._read_disk_cache=lambda *args, **kwargs: None; wb._write_disk_cache=lambda *args: None
        wb.fetch_wb_tariffs=AsyncMock(return_value={'subjects':{}, 'warehouses':{}})
        async def request(method,url,params=None,json_data=None):
            if '/list/goods/filter' in url:
                return {'data':{'listGoods':[{'nmID':123,'vendorCode':'1001','sizes':[{'discountedPrice':299}]}]}}
            if '/cards/list' in url:
                return {'cards':[{'nmID':123,'sizes':[{'skus':['abc']}]}], 'cursor':{'total':1}}
            if '/warehouses' in url: return [{'id':1},{'id':1},{'id':2}]
            if '/stocks/' in url: return {'stocks':[{'sku':'abc','amount':2},{'sku':'abc','amount':2}]}
            if url.endswith('/supplier/stocks'):
                from services.common.market_api import APIStatusError
                raise APIStatusError(404, 'use analytics')
            if url.endswith('/warehouse_remains'): return {'data':{'taskId':'test'}}
            if url.endswith('/status'): return {'data':{'status':'done'}}
            if url.endswith('/download'): return [{'nmId':123,'barcode':'abc','techSize':'0','warehouses':[{'warehouseName':'Всего находится на складах','quantity':5}]}]
            if '/supplier/orders' in url: return []
            if '/analytics/v1/order-feed' in url: return {'orders': [], 'snapshotTime': '2026-11-18T00:00:00Z'}
            raise AssertionError(url)
        wb._request=AsyncMock(side_effect=request)
        rows=await wb.fetch_stocks_and_prices(); self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['stock_fbo'],rows[0]['stock_fbs']),(5,4))

    async def test_ozon_payload(self):
        oz=OzonSellerAPI(); oz._request=AsyncMock(return_value={'result':[{'offer_id':'1001','updated':True}]})
        await oz.update_price('123','1001',299)
        item=oz._request.await_args.args[2]['prices'][0]
        self.assertEqual(item, {'product_id':123, 'offer_id':'1001', 'price':'299.00', 'auto_action_enabled':'UNKNOWN'})

    async def test_unauthorized(self):
        from types import SimpleNamespace
        call=SimpleNamespace(from_user=SimpleNamespace(id=999), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data='act:app:1')
        await cb_approve_action(call)
        self.assertTrue(call.answer.await_args.kwargs['show_alert'])

    async def test_timeout_does_not_retry_or_unblock_group(self):
        from types import SimpleNamespace
        import uuid
        group=uuid.uuid4().hex
        a=await create_pending_action('ozon','1001','update_price',{'group_id':group,'offer_id':'1001','product_id':'123','new_price':299})
        b=await create_pending_action('ozon','1001','update_price',{'group_id':group,'offer_id':'1001','product_id':'123','new_price':500})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:app:{a.id}')
        with patch('bot.handlers.callbacks.ozon_api.update_price',new=AsyncMock(side_effect=TimeoutError('timeout'))) as update:
            await cb_approve_action(call)
            self.assertEqual(update.await_count,1)
        self.assertEqual((await get_pending_action(a.id)).status,'unknown')
        self.assertEqual((await get_pending_action(b.id)).status,'unknown')
        self.assertIsNone(await claim_price_action(a.id))

    async def test_rejection_closes_both(self):
        from types import SimpleNamespace
        import uuid
        group=uuid.uuid4().hex
        a=await create_pending_action('ozon','1001','update_price',{'group_id':group})
        b=await create_pending_action('ozon','1001','update_price',{'group_id':group})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:rej:{a.id}')
        await cb_reject_action(call)
        self.assertEqual((await get_pending_action(a.id)).status,'rejected')
        self.assertEqual((await get_pending_action(b.id)).status,'rejected')

    async def test_callback_ozon_and_wb(self):
        from types import SimpleNamespace
        a=await create_pending_action('ozon','1001','update_price',{'offer_id':'1001','product_id':'123','new_price':299})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:app:{a.id}')
        with patch('bot.handlers.callbacks.ozon_api.update_price',new=AsyncMock(return_value={'result':[{'offer_id':'1001','updated':True,'errors':[]}]})):
            await cb_approve_action(call)
        self.assertEqual((await get_pending_action(a.id)).status,'executed')
        b=await create_pending_action('wb','1001','update_price',{'nm_id':123,'new_price':299})
        call.data=f'act:app:{b.id}'
        with patch('bot.handlers.callbacks.wb_api.update_price_and_min_price',new=AsyncMock(return_value={'status':'submitted','upload_id':42,'min_price_for_promotions':275})):
            await cb_approve_action(call)
        self.assertEqual((await get_pending_action(b.id)).status,'submitted')
        self.assertIn('ещё не подтверждена', call.message.reply.await_args.args[0])

if __name__ == '__main__': unittest.main()
