"""Consistent bottom navigation for completed bot screens."""
def footer(state, rows, back='home'):
    def button(text, action):
        return {'text': text, 'callback_data': f"h:{state['rev']}:{action}"}
    return {'inline_keyboard': [*rows, [button('⬅️', back), button('🏠', 'home')]]}
