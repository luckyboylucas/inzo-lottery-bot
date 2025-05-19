from flask import Flask
from threading import Thread
import discord
from discord.ext import commands, tasks
import json
import os
import random
from datetime import datetime, timedelta

# --- KEEP ALIVE WEB SERVER ---
app = Flask('')

@app.route('/')
def home():
    print("✅ UptimeRobot ping ontvangen!")
    return "InzoLotto bot is alive!"


def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- CONFIG ---
TICKET_PRICE_USD = 0.25
TICKET_PRICE_ROBUX = 20
ADMIN_IDS = [667010067585040390, 855507814352158730]
DRAW_CHANNEL_NAME = "inzo-lotto-result"
COMMAND_CHANNEL_NAME = "lotto-commands"
LOG_CHANNEL_NAME = "lotto-log"
DATA_FILE = "lotto_data.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- HELPERS ---
def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "tickets": {},
            "pot_usd": 0.0,
            "pot_robux": 0,
            "drawn_numbers": [],
            "round": 1,
            "last_draw": None
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def generate_numbers():
    return sorted(random.sample(range(1, 51), 5))

def format_currency(amount, method):
    if method == "usd":
        return f"${amount:.2f} USD"
    elif method == "robux":
        return f"{amount} Robux"
    return str(amount)

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not lotto_drawer.is_running():
        lotto_drawer.start()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)

# --- COMMANDS ---
@bot.command()
async def buyticket(ctx):
    if ctx.channel.name != COMMAND_CHANNEL_NAME:
        await ctx.send(f"❌ Please use this command in #{COMMAND_CHANNEL_NAME}.")
        return

    data = load_data()
    user_id = str(ctx.author.id)
    if user_id in data["tickets"] and data["tickets"][user_id]["confirmed"]:
        await ctx.send("❌ You already have a confirmed ticket for this round.")
        return
    elif user_id in data["tickets"]:
        await ctx.send("❌ You already requested a ticket. Wait for admin confirmation.")
        return

    try:
        await ctx.author.send(
            f"Hi! Please reply with your payment method for the ticket:\n"
            f"`usd` for ${TICKET_PRICE_USD:.2f} USD\n"
            f"`robux` for {TICKET_PRICE_ROBUX} Robux\n\n"
            f"Reply with `usd` or `robux`."
        )
    except:
        await ctx.send("❌ I couldn't DM you. Please enable DMs and try again.")
        return

    def check(m):
        return m.author == ctx.author and isinstance(m.channel, discord.DMChannel) and m.content.lower() in ["usd", "robux"]

    try:
        reply = await bot.wait_for('message', check=check, timeout=120)
    except:
        await ctx.send("❌ Payment method timed out. Please try again.")
        return

    payment_method = reply.content.lower()

    if payment_method == "usd":
        await ctx.author.send("Please provide your PayPal username:")
    else:
        await ctx.author.send("Please provide your Roblox username:")

    def check_username(m):
        return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

    try:
        username_reply = await bot.wait_for('message', check=check_username, timeout=120)
    except:
        await ctx.send("❌ Username input timed out. Please try again.")
        return

    username = username_reply.content

    data["tickets"][user_id] = {"numbers": [], "confirmed": False, "payment_method": payment_method, "username": username}
    save_data(data)

    admin_mentions = ' '.join(f"<@{aid}>" for aid in ADMIN_IDS)
    await ctx.send(f"✅ Ticket requested! Admins {admin_mentions}, please confirm payment with `!confirmticket @user`.\nPayment: {payment_method.upper()}, Username: {username}")

@bot.command()
async def confirmticket(ctx, *, member_mention=None):
    if ctx.author.id not in ADMIN_IDS:
        await ctx.send("❌ Only admins can confirm tickets.")
        return
    if member_mention is None:
        await ctx.send("❌ Please mention a user, e.g. `!confirmticket @user`.")
        return
    try:
        member = await commands.MemberConverter().convert(ctx, member_mention)
    except:
        await ctx.send(f"❌ Could not find user: {member_mention}")
        return

    data = load_data()
    user_id = str(member.id)
    if user_id not in data["tickets"]:
        await ctx.send("❌ This user has not requested a ticket.")
        return
    if data["tickets"][user_id]["confirmed"]:
        await ctx.send("✅ This ticket is already confirmed.")
        return

    numbers = generate_numbers()
    data["tickets"][user_id]["numbers"] = numbers
    data["tickets"][user_id]["confirmed"] = True

    if data["tickets"][user_id]["payment_method"] == "usd":
        data["pot_usd"] += TICKET_PRICE_USD
    else:
        data["pot_robux"] += TICKET_PRICE_ROBUX

    save_data(data)
    await ctx.send(f"🎟️ Ticket confirmed for {member.mention}! Your numbers are: {', '.join(map(str, numbers))}")

@bot.command()
async def myticket(ctx):
    data = load_data()
    user_id = str(ctx.author.id)
    if user_id not in data["tickets"] or not data["tickets"][user_id]["confirmed"]:
        await ctx.send("❌ You have no confirmed ticket for this round.")
        return
    numbers = data["tickets"][user_id]["numbers"]
    await ctx.send(f"🎟️ Your ticket numbers: {', '.join(map(str, numbers))}")

@bot.command()
async def drawnumbers(ctx):
    if ctx.author.id not in ADMIN_IDS:
        await ctx.send("❌ Only admins can start the draw.")
        return
    await do_draw(ctx.guild, manual=True)

@bot.command()
async def results(ctx):
    if ctx.author.id not in ADMIN_IDS:
        await ctx.send("❌ Only admins can view the results.")
        return
    await show_results(ctx.guild)

purchased_tickets = []

@bot.command()
@commands.has_permissions(administrator=True)
async def lottopurge(ctx):
    lotto_channel = discord.utils.get(ctx.guild.text_channels, name="lotto-commands")
    log_channel = discord.utils.get(ctx.guild.text_channels, name="lotto-log")

    if not lotto_channel:
        await ctx.send("❌ Channel `#lotto-commands` not found.")
        return
    if not log_channel:
        await ctx.send("❌ Channel `#lotto-log` not found.")
        return

    deleted_count = 0
    async for message in lotto_channel.history(limit=200):
        if not message.pinned:
            try:
                await message.delete()
                deleted_count += 1
            except Exception as e:
                print(f"Failed to delete message: {e}")

    await ctx.send(f"🧹 Deleted {deleted_count} messages in #lotto-commands.")

    data = load_data()
    tickets = data.get("tickets", {})
    confirmed_tickets = {uid: t for uid, t in tickets.items() if t.get("confirmed")}

    if confirmed_tickets:
        await log_channel.send("📥 **Tickets this round (before purge):**")
        for user_id, ticket in confirmed_tickets.items():
            member = ctx.guild.get_member(int(user_id))
            discord_name = member.name if member else "Unknown User"
            payment_method = ticket.get("payment_method", "Unknown")
            username = ticket.get("username", "Unknown")
            numbers = ticket.get("numbers", [])
            await log_channel.send(
                f"🎟️ **Discord:** {discord_name} | Paid via: **{payment_method}** | Username: **{username}** | Numbers: `{', '.join(map(str, numbers))}`"
            )
    else:
        await log_channel.send("ℹ️ No tickets were purchased this round.")

    data["pot_usd"] = 0
    data["pot_robux"] = 0
    data["tickets"] = {}
    save_data(data)

# --- DRAW ---
async def do_draw(guild, manual=False):
    data = load_data()
    confirmed_players = [uid for uid, t in data["tickets"].items() if t["confirmed"]]
    if len(confirmed_players) < 3 and not manual:
        channel = discord.utils.get(guild.text_channels, name=COMMAND_CHANNEL_NAME)
        if channel:
            await channel.send("❌ Not enough confirmed players to draw (minimum 3 required).")
        return

    drawn = generate_numbers()
    data["drawn_numbers"] = drawn
    data["last_draw"] = datetime.utcnow().isoformat()

    total_usd = data["pot_usd"]
    total_robux = data["pot_robux"]
    prize_usd = round(total_usd * 0.9, 2)
    prize_robux = int(total_robux * 0.9)

    results = []
    for uid, ticket in data["tickets"].items():
        if ticket["confirmed"]:
            matches = len(set(ticket["numbers"]) & set(drawn))
            results.append((uid, matches))

    results.sort(key=lambda x: x[1], reverse=True)
    winners = []
    top_scores = []
    for uid, score in results:
        if score == 0:
            continue
        if len(winners) >= 3:
            break
        if not top_scores or score == top_scores[0]:
            winners.append((uid, score))
            if not top_scores:
                top_scores.append(score)
        elif score == top_scores[-1]:
            winners.append((uid, score))
            top_scores.append(score)
        else:
            break

    draw_channel = discord.utils.get(guild.text_channels, name=DRAW_CHANNEL_NAME)
    if not draw_channel:
        draw_channel = discord.utils.get(guild.text_channels, name=COMMAND_CHANNEL_NAME)

    if not winners:
        await draw_channel.send(f"⚠️ No winners this round. The pot will roll over to the next round.")
    else:
        message = f"🎉 **Lotto Draw - Round {data['round']}**\nDrawn numbers: {', '.join(map(str, drawn))}\n\n"
        prize_per_winner_usd = prize_usd / len(winners) if prize_usd > 0 else 0
        prize_per_winner_robux = prize_robux // len(winners) if prize_robux > 0 else 0

        places = ["🥇 1st place", "🥈 2nd place", "🥉 3rd place"]
        for i, (uid, score) in enumerate(winners):
            user = await bot.fetch_user(int(uid))
            pay_str = []
            if prize_per_winner_usd > 0:
                pay_str.append(f"${prize_per_winner_usd:.2f} USD")
            if prize_per_winner_robux > 0:
                pay_str.append(f"{prize_per_winner_robux} Robux")
            message += f"{places[i]}: {user.mention} - {score} matching numbers - Won: {', '.join(pay_str)}\n"
        await draw_channel.send(message)

    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if log_channel:
        await log_channel.send(f"Draw Round {data['round']} - Numbers: {', '.join(map(str, drawn))}")

    data["round"] += 1
    data["tickets"] = {}
    data["pot_usd"] = prize_usd if not winners else 0.0
    data["pot_robux"] = prize_robux if not winners else 0
    data["drawn_numbers"] = []
    save_data(data)

async def show_results(guild):
    data = load_data()
    drawn = data.get("drawn_numbers", [])
    round_num = data.get("round", 1) - 1
    if not drawn:
        return
    draw_channel = discord.utils.get(guild.text_channels, name=DRAW_CHANNEL_NAME)
    if draw_channel:
        await draw_channel.send(f"📊 **Lotto Results - Round {round_num}**\nDrawn numbers: {', '.join(map(str, drawn))}")

@tasks.loop(hours=24)
async def lotto_drawer():
    data = load_data()
    last = datetime.fromisoformat(data["last_draw"]) if data["last_draw"] else datetime.utcnow() - timedelta(days=14)
    if (datetime.utcnow() - last).days >= 14:
        for guild in bot.guilds:
            await do_draw(guild)
app = Flask('')

@app.route('/')
def home():
    return "InzoLotto bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()


# --- RUN ---
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("❌ No Discord token found in environment variables!")
    exit()

keep_alive()
bot.run(TOKEN)
