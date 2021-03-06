# coding=utf-8
from unittest import TestCase
from cStringIO import StringIO
from socketio import has_bin
from socketio.binary import Binary


class BinaryTest(TestCase):

    def test_deconstruct_packet(self):
        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': 'what the hell'
        })

        self.assertEqual(len(results['buffers']), 0)

        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': bytearray([0, 1, 2])
        })
        print results["packet"]

        self.assertEqual(results['packet']['attachments'], 1)
        self.assertEqual(len(results['buffers']), 1)

        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': [
                'what',
                bytearray([0, 1, 2, 3]),
                'the',
                bytearray([4, 5]),
                'hell'
            ]
        })

        print results['packet']
        self.assertEqual(len(results['buffers']), 2)

        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': {
                'hello': bytearray([0, 1, 2, 3]),
                'world': bytearray([4, 5]),
                'yes': '!!'
            }
        })

        self.assertEqual(len(results['buffers']), 2)

    def test_reconstruct_packet(self):
        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': 'what the hell'
        })

        packet = Binary.reconstruct_packet(results['packet'], results['buffers'])

        self.assertEqual(packet['type'], 'event')
        self.assertEqual(packet['data'], 'what the hell')

        results = Binary.deconstruct_packet({
                    'type': 'event',
                    'data': bytearray([0, 1, 2])
                })
        packet = Binary.reconstruct_packet(results['packet'], results['buffers'])
        self.assertEqual(packet['data'], bytearray([0, 1, 2]))

        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': [
                'what',
                bytearray([0, 1, 2, 3]),
                'the',
                bytearray([4, 5]),
                'hell'
            ]
        })
        packet = Binary.reconstruct_packet(results['packet'], results['buffers'])
        self.assertEqual(packet['data'][1], bytearray([0, 1, 2, 3]))


        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': {
                'hello': bytearray([0, 1, 2, 3]),
                'world': bytearray([4, 5]),
                'yes': '!!'
            }
        })
        packet = Binary.reconstruct_packet(results['packet'], results['buffers'])
        self.assertEqual(packet['data']['world'], bytearray([4, 5]))

        results = Binary.deconstruct_packet({
            'type': 'event',
            'data': {
                'hello': 'what',
                'world': bytearray([4, 5]),
                'yes': True
            }
        })
        packet = Binary.reconstruct_packet(results['packet'], results['buffers'])
        self.assertEqual('what', packet['data']['hello'])

    def test_remove_blobs(self):
        data = 'hello'
        data = Binary.remove_blobs(data)
        self.assertEqual(data, 'hello')

        data = StringIO('hello')
        data = Binary.remove_blobs(data)
        self.assertEqual(type(data), bytearray)

        data = Binary.remove_blobs(['hello', bytearray([1,2]), StringIO('hi')])
        self.assertEqual(type(data[2]), bytearray)

    def test_has_bin(self):
        self.assertTrue(has_bin({'what': bytearray('ahahah')}))
