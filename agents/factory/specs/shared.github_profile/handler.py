"""Read only the authenticated profile; account selection belongs to Connections."""
def run(input, ctx):
    if not input.get('action'):
        return {'status': 'needs_input', 'detail': 'Choose describe or read.'}
    if input['action'] == 'describe':
        return {'status': 'ok', 'output': {'requirement': 'connector.read:github:profile'}}
    receipt = ctx.connector_read('github', 'profile')
    return {'status': 'ok', 'output': {'profile': receipt['result'], 'receipt': receipt['id']}}
