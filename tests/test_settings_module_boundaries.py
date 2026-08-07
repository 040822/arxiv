import unittest

import source.settings
from source.settings import thinking


class SettingsModuleBoundaryTests(unittest.TestCase):
    def test_thinking_interface_is_implemented_by_thinking_module(self):
        self.assertIs(source.settings.build_chat_completion_kwargs, thinking.build_chat_completion_kwargs)
        self.assertIs(source.settings.get_thinking_protocol, thinking.get_thinking_protocol)
        self.assertIs(source.settings.normalize_provider_config, thinking.normalize_provider_config)


if __name__ == "__main__":
    unittest.main()
