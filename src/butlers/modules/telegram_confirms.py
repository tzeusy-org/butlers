"""Messenger-owned provider adapter for the signed informational confirm service.

Constructed by trusted runtime composition with Messenger's Telegram module. Source
butlers never receive the module, provider client, token or raw send callables.
"""

from butlers.core.attention_ledger import check_owner_notify_suppression
from butlers.core.confirm_delivery import ConfirmDelivery
from butlers.core.permissions import NOTIFY_PERMISSION, require_permission
from butlers.telegram_text import text_units


def messenger_confirm_delivery(service, telegram, *, allowed):
    async def send(row, keyboard):
        data = await telegram._send_message(
            row["recipient_chat"],
            row["prompt"]["text"],
            reply_markup=keyboard,
            render_links_inert=True,
            server_built_reply_markup=True,
        )
        reference = data.get("result", {}).get("message_id")
        if type(reference) is not int or reference <= 0:
            return {"status": "ambiguous"}
        return {"status": "confirmed", "reference": str(reference)}

    async def edit(row, text):
        await require_permission(
            telegram._permission_pool(), telegram._butler_name, NOTIFY_PERMISSION, fail_closed=True
        )
        # One literal edit removes the keyboard atomically. A lost successful edit
        # can be retried: Telegram's exact already-current receipt is idempotent.
        response = await telegram._get_client().post(
            f"{telegram._base_url()}/editMessageText",
            json={
                "chat_id": row["recipient_chat"],
                "message_id": int(row["message_ref"]),
                "text": text,
                "entities": [{"type": "pre", "offset": 0, "length": text_units(text)}],
                "link_preview_options": {"is_disabled": True},
                "reply_markup": {"inline_keyboard": []},
            },
        )
        data = response.json()
        if (
            response.status_code == 400
            and data.get("ok") is False
            and data.get("error_code") == 400
            and data.get("description")
            in {
                "Bad Request: message is not modified",
                "Bad Request: message is not modified: specified new message content and reply "
                "markup are exactly the same as a current content and reply markup of the message",
            }
        ):
            return True
        response.raise_for_status()
        return data.get("ok") is True

    async def may_deliver(row):
        # A producer's callback cannot bypass shared owner quiet/sleep policy.
        # Returning False retains the durable new prompt for a later worker tick.
        if row["recipient_owner"] and await check_owner_notify_suppression(
            telegram._permission_pool(), log_context="confirm"
        ):
            return False
        return await allowed(row)

    return ConfirmDelivery(service, send=send, edit=edit, allowed=may_deliver)
