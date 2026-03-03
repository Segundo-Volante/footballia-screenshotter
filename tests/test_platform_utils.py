"""Tests for cross-platform utilities."""


class TestPlatformUtils:

    def test_get_data_dir_exists(self):
        from backend.platform_utils import get_data_dir
        d = get_data_dir()
        assert d.exists()
        assert d.is_dir()

    def test_find_available_port(self):
        from backend.platform_utils import find_available_port
        port = find_available_port(preferred=18000)
        assert 18000 <= port < 18100

    def test_get_platform_info(self):
        from backend.platform_utils import get_platform_info
        info = get_platform_info()
        assert "os" in info
        assert "python" in info
        assert "drm_bypass_reliable" in info

    def test_check_dependencies(self):
        from backend.platform_utils import check_dependencies
        issues = check_dependencies()
        assert isinstance(issues, list)
        for issue in issues:
            assert "level" in issue
            assert "component" in issue
