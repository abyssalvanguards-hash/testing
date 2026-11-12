import discord, io, urllib.parse, re, asyncio
import aiohttp
from discord.ext import commands
from core import db
from core.security import is_bot_admin, new_reference
from core.logging import audit
from datetime import datetime, timezone
try:
    import qrcode
except ImportError:
    qrcode=None

BLOCKCYPHER_BASE = {"btc": "https://api.blockcypher.com/v1/btc/main", "ltc": "https://api.blockcypher.com/v1/ltc/main"}
SATOSHIS = 100_000_000

class Payments(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @commands.hybrid_command(name="paymentconfig")
    async def config_payment(self,ctx,method,value,*,extra=""):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        method=method.lower()
        if method=="upi":
            await db.set_setting(ctx.guild.id,"upi_id",value)
            await db.set_setting(ctx.guild.id,"upi_payee",extra or "@h_two_")
        elif method in ("ltc","btc"):
            await db.set_setting(ctx.guild.id,method+"_address",value)
        else: return await ctx.reply("Methods: upi, ltc, btc.")
        await ctx.reply(f"{method.upper()} payment instructions updated. Verify the configured address independently before sharing it.")

    @commands.hybrid_command(name="invoice")
    async def invoice(self,ctx,amount:float,method):
        if amount<=0 or amount>1000000: return await ctx.reply("Enter a valid positive amount.")
        method=method.lower()
        if method not in ("upi","ltc","btc"): return await ctx.reply("Use upi, ltc, or btc.")
        if method in ("btc", "ltc") and round(amount, 8) != amount:
            return await ctx.reply("Crypto invoice amounts support up to 8 decimal places. Amounts are denominated in the selected coin, not INR.")
        ref=new_reference()
        await db.execute("INSERT INTO invoices(guild_id,user_id,method,amount,reference,created_at) VALUES(?,?,?,?,?,?)",
            (ctx.guild.id,ctx.author.id,method,amount,ref,datetime.now(timezone.utc).isoformat()))
        if method=="upi":
            upi=await db.get_setting(ctx.guild.id,"upi_id")
            payee=await db.get_setting(ctx.guild.id,"upi_payee","WardenX Manager")
            if not upi: return await ctx.reply("UPI is not configured by the server owner.")
            uri="upi://pay?"+urllib.parse.urlencode({"pa":upi,"pn":payee,"am":f"{amount:.2f}","cu":"INR","tn":f"Invoice {ref}"})
            embed=discord.Embed(title=f"UPI Invoice • {ref}",description=f"Amount: ₹{amount:.2f}\nUPI ID: `{upi}`\nPayee: {payee}\nReference: `{ref}`\n\nPayment is manually verified by staff.",color=discord.Color.blurple())
            if qrcode:
                img=qrcode.make(uri); buff=io.BytesIO(); img.save(buff,format="PNG"); buff.seek(0)
                await ctx.reply(embed=embed,file=discord.File(buff,filename="upi-qr.png"))
            else: await ctx.reply(embed=embed)
        else:
            address=await db.get_setting(ctx.guild.id,method+"_address")
            if not address: return await ctx.reply(f"{method.upper()} address is not configured.")
            await ctx.reply(f"**{method.upper()} invoice {ref}**\nAmount: `{amount:.8f} {method.upper()}`\nAddress: `{address}`\nAfter sending, submit the transaction ID to staff using `{ctx.prefix}paymentcheck <invoice_id> <txid>`. Automatic checks require at least 2 confirmations.")
        await audit(self.bot,ctx.guild,"Payment invoice created",f"Invoice {ref} • {method} • {amount}",ctx.author)

    @commands.hybrid_command(name="paymentcheck")
    async def payment_check(self,ctx,invoice_id:int,txid:str):
        """Read-only chain lookup, then mark invoice verified only on exact checks."""
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        row=await db.fetchone("SELECT * FROM invoices WHERE id=? AND guild_id=?",(invoice_id,ctx.guild.id))
        if not row or row["status"]!="pending": return await ctx.reply("Invoice not found or already processed.")
        coin=row["method"]
        if coin not in BLOCKCYPHER_BASE: return await ctx.reply("Automatic verification is supported for BTC and LTC only. UPI remains manual.")
        if not re.fullmatch(r"[0-9a-fA-F]{64}",txid): return await ctx.reply("Enter a valid 64-character transaction ID.")
        if await db.fetchone("SELECT txid FROM used_payment_txids WHERE txid=?",(txid.lower(),)):
            return await ctx.reply("That transaction ID has already been used for another invoice.")
        address=await db.get_setting(ctx.guild.id,coin+"_address")
        if not address: return await ctx.reply(f"The server's {coin.upper()} receiving address is not configured.")
        url=f"{BLOCKCYPHER_BASE[coin]}/txs/{txid.lower()}"
        try:
            timeout=aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return await ctx.reply(f"Explorer lookup failed (HTTP {response.status}). No payment status was changed.")
                    data=await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return await ctx.reply("Could not reach the blockchain explorer. No payment status was changed.")
        confirmations=int(data.get("confirmations") or 0)
        outputs=data.get("outputs") or []
        received=sum(int(out.get("value") or 0) for out in outputs if address in (out.get("addresses") or []))
        expected=int(round(float(row["amount"])*SATOSHIS))
        if data.get("hash", "").lower()!=txid.lower(): return await ctx.reply("Explorer returned a different transaction ID. Not verified.")
        if received < expected: return await ctx.reply(f"Insufficient amount to the configured address: received {received/SATOSHIS:.8f} {coin.upper()}, expected {expected/SATOSHIS:.8f}. Not verified.")
        if confirmations < 2: return await ctx.reply(f"Transaction found, but it has only {confirmations} confirmation(s). At least 2 are required. Invoice remains pending.")
        try:
            await db.execute("INSERT INTO used_payment_txids(txid,invoice_id,guild_id,used_at) VALUES(?,?,?,?)",(txid.lower(),invoice_id,ctx.guild.id,datetime.now(timezone.utc).isoformat()))
            await db.execute("UPDATE invoices SET status='verified',txid=?,confirmations=? WHERE id=? AND status='pending'",(txid.lower(),confirmations,invoice_id))
        except Exception:
            return await ctx.reply("Could not safely reserve this transaction ID (it may have been used concurrently). Invoice remains pending; ask another admin to review.")
        await audit(self.bot,ctx.guild,"Crypto payment automatically verified",f"Invoice {row['reference']} • {coin.upper()} • {received/SATOSHIS:.8f} • {confirmations} confirmations • tx {txid}",ctx.author)
        await ctx.reply(f"Payment verified: invoice `{row['reference']}` received `{received/SATOSHIS:.8f} {coin.upper()}` with {confirmations} confirmations. Transaction ID recorded to prevent reuse.")

    @commands.hybrid_command(name="paymentpending")
    async def pending(self,ctx):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        rows=await db.fetchall("SELECT * FROM invoices WHERE guild_id=? AND status='pending' ORDER BY id DESC LIMIT 20",(ctx.guild.id,))
        await ctx.reply("\n".join(f"#{r['id']} {r['reference']} • <@{r['user_id']}> • {r['method']} • {r['amount']}" for r in rows) or "No pending invoices.")

    @commands.hybrid_command(name="paymentverify")
    async def verify(self,ctx,invoice_id:int):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        row=await db.fetchone("SELECT * FROM invoices WHERE id=? AND guild_id=?",(invoice_id,ctx.guild.id))
        if not row or row["status"]!="pending": return await ctx.reply("Invoice not found or already processed.")
        await db.execute("UPDATE invoices SET status='verified' WHERE id=?",(invoice_id,))
        await audit(self.bot,ctx.guild,"Payment manually verified",f"Invoice {row['reference']}",ctx.author)
        await ctx.reply(f"Invoice {row['reference']} marked verified manually. Use only after independently confirming receipt.")

async def setup(bot): await bot.add_cog(Payments(bot))
