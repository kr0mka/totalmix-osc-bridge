"""Offline HTTP-handler regressions: no sockets or live TotalMix commands."""
import unittest
from unittest.mock import patch

import bridge


class FakeTotalMix:
    def __init__(self, room, peq, incomplete=False):
        self.enabled = {4: room, 2: peq}
        self.page = 4
        self.commands = []
        self.incomplete = incomplete

    def send_message(self, address, value):
        self.commands.append((address, value))
        if address.endswith('/busOutput'):
            self.page = int(address.split('/')[1])
        if address != '/setOffsetInBank':
            return
        enabled = self.enabled[self.page]
        if enabled is None:
            return
        prefix, bands = ('req', 9) if self.page == 4 else ('eq', 3)
        bridge.osc_cache[f'/{self.page}/{prefix}Enable'] = enabled
        for i in range(1, bands + 1):
            for field, value in {'Freq': 0.5, 'Gain': 0.6 if i == 1 else 0.5,
                                 'Q': 0.5, 'Type': 0.0}.items():
                if field == 'Type' and i not in ((1, 8, 9) if self.page == 4 else (1, 3)):
                    continue
                if not (self.incomplete and field == 'Freq'):
                    bridge.osc_cache[f'/{self.page}/{prefix}{field}{i}'] = value


class ReadTests(unittest.TestCase):
    def read(self, room, peq, channel=1, incomplete=False):
        # Deliberately seed stale enabled state from a previous channel.
        bridge.osc_cache.clear()
        bridge.osc_cache.update({'/4/reqEnable': 1.0, '/2/eqEnable': 1.0})
        fake = FakeTotalMix(room, peq, incomplete)
        handler = object.__new__(bridge.BridgeHandler)
        handler.path = f'/api/channel/{channel}/eq'
        result = []
        handler.send_json = lambda data, status=200: result.append((status, data))
        with patch.object(bridge, 'osc_client', fake), \
             patch.object(bridge.time, 'sleep'), \
             patch.object(bridge.time, 'monotonic', side_effect=[i / 10 for i in range(100)]):
            handler.do_GET()
        self.assertTrue(all(address in ('/4/busOutput', '/2/busOutput',
                                       '/setBankStart', '/setOffsetInBank')
                            for address, _ in fake.commands))
        return result[0], fake.commands

    def test_all_enable_combinations(self):
        for room, peq, count in [(0, 0, 0), (1, 0, 1), (0, 1, 1), (1, 1, 2)]:
            with self.subTest(room=room, peq=peq):
                (status, data), _ = self.read(room, peq)
                self.assertEqual(status, 200)
                self.assertEqual(len(data['filters']), count)

    def test_missing_feedback_cannot_reuse_previous_enable_state(self):
        for room, peq in [(None, 1), (1, None)]:
            (status, data), _ = self.read(room, peq)
            self.assertEqual(status, 504)
            self.assertNotIn('filters', data)

    def test_incomplete_enabled_section_fails_instead_of_inventing_filters(self):
        (status, _), _ = self.read(1, 1, incomplete=True)
        self.assertEqual(status, 504)

    def test_disabled_section_does_not_require_band_feedback(self):
        (status, data), _ = self.read(0, 0, incomplete=True)
        self.assertEqual((status, data), (200, {'filters': []}))

    def test_channel_bank_selection(self):
        (_, _), commands = self.read(1, 0, channel=10)
        self.assertIn(('/setBankStart', 8.0), commands)
        self.assertIn(('/setOffsetInBank', 1.0), commands)

    def test_invalid_channel_sends_nothing(self):
        (status, _), commands = self.read(1, 1, channel=0)
        self.assertEqual(status, 400)
        self.assertEqual(commands, [])

    def test_real_peq_feedback_has_no_middle_band_type(self):
        fake = FakeTotalMix(0, 1)
        handler = object.__new__(bridge.BridgeHandler)
        handler.path = '/api/channel/1/eq'
        result = []
        handler.send_json = lambda data, status=200: result.append((status, data))

        def feedback_after_settling(seconds):
            if seconds == 0.15 and fake.page == 2:
                bridge.osc_cache.update({
                    '/2/eqEnable': 1.0,
                    '/2/eqGain1': 0.7, '/2/eqGain2': 0.375, '/2/eqGain3': 0.3,
                    '/2/eqType1': 1 / 3, '/2/eqType3': 1 / 3,
                    '/2/eqQ1': 0.0, '/2/eqQ3': 0.0,
                })

        # An early disabled packet must not cause return before settling.
        fake.enabled[2] = 0
        bridge.osc_cache.clear()
        with patch.object(bridge, 'osc_client', fake), \
             patch.object(bridge.time, 'sleep', side_effect=feedback_after_settling):
            handler.do_GET()
        status, data = result[0]
        self.assertEqual(status, 200)
        self.assertEqual([f['type'] for f in data['filters']], ['LSQ', 'PK', 'HSQ'])
        self.assertEqual([f['gain'] for f in data['filters']], [8.0, -5.0, -8.0])
        self.assertEqual(data['filters'][0]['q'], 0.4)


if __name__ == '__main__':
    unittest.main()
