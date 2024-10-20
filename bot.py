import os
import telebot
from datetime import datetime, timedelta
import time
from pymongo import MongoClient
import threading
import logging
from tenacity import retry, stop_after_attempt, wait_fixed

# Configuration from environment variables for security
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MONGO_URI = os.getenv('MONGODB_URI')
OWNER_ID = os.getenv('OWNER_ID')  # Add the owner ID to restrict admin commands

bot = telebot.TeleBot(TOKEN)

# Connect to MongoDB with connection pooling
client = MongoClient(MONGO_URI, maxPoolSize=10)
db = client['telegram_bot']
premium_collection = db['premium_users']
channels_collection = db['premium_channels']

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Function to check if the bot is an admin in a channel
def is_bot_admin(chat_id):
    try:
        chat_member = bot.get_chat_member(chat_id, bot.get_me().id)
        return chat_member.status in ['administrator', 'creator']
    except Exception as e:
        logging.error(f"Failed to check if bot is admin in chat {chat_id}: {e}")
        return False

# Command to add a new premium channel by ID (restricted to the bot owner)
@bot.message_handler(commands=['addchannel'])
def handle_add_channel(message):
    if str(message.from_user.id) != OWNER_ID:
        bot.reply_to(message, "You do not have permission to use this command.")
        return

    try:
        # Extract the channel ID from the message
        channel_id = int(message.text.split()[1])
        
        # Get chat info to validate and check if bot is an admin
        chat = bot.get_chat(channel_id)
        if is_bot_admin(chat.id):
            # Add the channel to the database if the bot is an admin
            existing_channel = channels_collection.find_one({'chat_id': channel_id})
            if existing_channel:
                bot.reply_to(message, f"The channel with ID {channel_id} is already in the premium list.")
            else:
                channels_collection.insert_one({'chat_id': chat.id, 'title': chat.title})
                bot.reply_to(message, f"The channel '{chat.title}' (ID: {chat.id}) has been added to the premium list.")
                logging.info(f"Added new premium channel: {chat.title} (ID: {chat.id})")
        else:
            bot.reply_to(message, f"The bot is not an admin in the channel with ID {channel_id}. Please make the bot an admin first.")
    except IndexError:
        bot.reply_to(message, "Usage: /addchannel <channel_id>")
    except ValueError:
        bot.reply_to(message, "Invalid channel ID. Please provide a valid numerical channel ID.")
    except Exception as e:
        logging.error(f"Error in /addchannel command: {e}")
        bot.reply_to(message, "Failed to add the channel. Please ensure the channel ID is correct.")

# Function to retrieve all premium channel IDs from MongoDB
def get_premium_channels():
    channels = channels_collection.find()
    return [{'chat_id': channel['chat_id'], 'title': channel['title']} for channel in channels]

# Function to handle user join requests for premium channels
@bot.message_handler(commands=['channels'])
def handle_channels(message):
    user_id = str(message.from_user.id)
    user_data = premium_collection.find_one({'user_id': user_id})
    if user_data:
        expiry = user_data['expiry_date']
        if expiry >= datetime.now():
            channels = get_premium_channels()
            if channels:
                bot.send_message(user_id, "Here are the channels you have access to. Request access through the links:")
                for channel in channels:
                    join_request_link = f"https://t.me/c/{channel['chat_id']}?joinrequest=1"
                    bot.send_message(user_id, f"{channel['title']}: {join_request_link}")
            else:
                bot.send_message(user_id, "No premium channels are currently available.")
        else:
            bot.send_message(user_id, "Your premium access has expired.")
    else:
        bot.send_message(user_id, "You are not a premium user.")

# Function to handle new join request approvals for premium channels
@bot.message_handler(content_types=['chat_join_request'])
def handle_join_request(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if the user is in the premium list
    user_data = premium_collection.find_one({'user_id': str(user_id)})
    if user_data and user_data['expiry_date'] >= datetime.now():
        try:
            bot.approve_chat_join_request(chat_id, user_id)
            bot.send_message(user_id, "Your join request has been approved. Welcome to the channel!")
            logging.info(f"Approved join request for user {user_id} in chat {chat_id}")
        except Exception as e:
            logging.error(f"Failed to approve join request for user {user_id} in chat {chat_id}: {e}")
            bot.send_message(user_id, "Failed to approve your join request. Please try again later.")
    else:
        try:
            bot.decline_chat_join_request(chat_id, user_id)
            bot.send_message(user_id, "You do not have a valid premium subscription to join this channel.")
            logging.info(f"Declined join request for user {user_id} in chat {chat_id} due to expired or missing subscription")
        except Exception as e:
            logging.error(f"Failed to decline join request for user {user_id} in chat {chat_id}: {e}")

# Other existing functions remain the same...

# Periodic check to remove expired users every 24 hours and send reminders
def run_expiry_check():
    while True:
        try:
            remove_expired_users()
            send_expiry_reminders()
        except Exception as e:
            logging.error(f"Error during expiry check or reminders: {e}")
        time.sleep(86400)  # Check once per day

# Start the expiry check in a new thread
expiry_thread = threading.Thread(target=run_expiry_check)
expiry_thread.start()

# Start the bot using polling
bot.polling()
