def test_simple(mock_main_window):
    assert mock_main_window.page_info_label is not None
    mock_main_window.page_info_label.setText("Hello")
    assert mock_main_window.page_info_label.text() == "Hello"
