"""AirPlay routing/ownership regressions; never talks to Music or hardware."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
import airplay

class AirPlayTests(unittest.TestCase):
    def player(self):
        with patch.object(Path,'read_text',side_effect=OSError):
            return airplay.AirPlayPlayer()

    def test_no_selection_cannot_fall_back_to_system_output(self):
        p=self.player()
        with patch.object(airplay,'music') as m:
            with self.assertRaises(RuntimeError):p.play(b'abc')
            m.assert_not_called()

    def test_stop_only_with_owned_track_id(self):
        p=self.player()
        with patch.object(airplay,'music') as m:
            p.stop();m.assert_not_called()
            p.track_id='own-id';p.stop()
            m.assert_called_once_with({'action':'stop','id':'own-id'})
            self.assertIsNone(p.track_id)

    def test_invalid_id_rejected_before_any_subprocess(self):
        with patch.object(airplay.subprocess,'run') as m:
            with self.assertRaises(ValueError):airplay.music({'action':'select','uid':'1; execute'})
            m.assert_not_called()

    def test_offline_output_is_an_error_not_a_success(self):
        p=self.player()
        with patch.object(airplay,'music',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):p.select('123')
            self.assertEqual(p.uid,'')

    def test_one_shot_end_stops_before_eof(self):
        p=self.player();p.track_id='own'
        with patch.object(airplay,'music',return_value={'id':'own','position':4.5}) as m:
            p._finish_once(0,'own',4)
            self.assertIsNone(p.track_id)
            self.assertEqual(m.call_args.args[0],{'action':'stop','id':'own'})

    def test_old_generation_cannot_stop_new_playback(self):
        p=self.player();p._generation=2;p.track_id='new'
        with patch.object(airplay,'music') as m:
            p._finish_once(1,'old',4)
            m.assert_not_called()
            self.assertEqual(p.track_id,'new')

    def test_user_taking_over_is_not_stopped(self):
        p=self.player();p.track_id='own'
        with patch.object(airplay,'music',return_value={'id':'user','position':7}) as m:
            p._finish_once(0,'own',4)
            self.assertEqual(m.call_count,1)
            self.assertEqual(m.call_args.args[0],{'action':'state'})

    def test_host_tts_and_track_route_to_airplay(self):
        with patch('audio_engine.get_bt_status',return_value={'connected':False,'devices':[]}):
            import bridge
        with patch.object(bridge.STATE,'audio_target','airplay'), patch.object(bridge.STATE,'volume',25), \
             patch.object(bridge,'synthesize_japanese_mp3',return_value=b'voice'), \
             patch.object(bridge.HOST_PLAYER,'stop'),patch.object(bridge.AIRPLAY,'play') as play, \
             patch.object(bridge,'upload_clip') as upload,patch.object(bridge,'push_state') as feed:
            bridge.STATE.last_audio_req={'old':True}
            bridge._play_or_upload_tts('日本語')
            play.assert_called_once_with(b'voice',volume=25)
            self.assertIsNone(bridge.STATE.last_audio_req)
            upload.assert_not_called()
            self.assertEqual(feed.call_args.kwargs['audio_cmd']['target'],'airplay')
            self.assertEqual(feed.call_args.kwargs['audio_cmd']['action'],'tts')
        with patch.object(bridge.STATE,'audio_target','airplay'),patch.object(bridge.STATE,'volume',25), \
             patch.object(bridge.HOST_PLAYER,'stop'),patch.object(bridge.AIRPLAY,'play') as play, \
             patch.object(bridge,'load_manifest',return_value={'tracks':[{'index':52,'duration':200}]}), \
             patch.object(bridge,'slice_track_mp3',return_value=(b'ab',2,5)),patch.object(bridge,'push_state'):
            bridge._start_track_playback(52,2,5,'loop')
            play.assert_called_once_with(b'ab',loop=True,volume=25)

if __name__=='__main__':unittest.main()
