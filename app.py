import os
from datetime import datetime, timedelta
import logging
from telegram import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    Update, 
    InputMediaPhoto,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    CallbackQuery,
    Message
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    InlineQueryHandler,
    ConversationHandler
)
import aiohttp
import asyncio
from aiohttp import web
import json
from urllib.parse import quote
import re
from typing import Dict, List, Optional, Union
import html

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
TELEGRAM_TOKEN = '7959729394:AAF5UJ4s5Z0gcy8qkJtdLsbDbIyM7TT6DB0'
TMDB_API_KEY = '69084ded6889a849708077681bd5dd7f'
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
PORT = 10000

# Conversation states
SELECTING_SEASON, SELECTING_EPISODE = range(2)

class MovieBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        self.data_manager = DataManager()
        self.rate_limiter = RateLimiter()
        self.session_manager = SessionManager()
        self.setup_handlers()
        
        # Start periodic tasks
        self.application.job_queue.run_repeating(
            self.cleanup_sessions,
            interval=timedelta(minutes=30)
        )

    def setup_handlers(self):
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("trending", self.trending_command))
        self.application.add_handler(CommandHandler("upcoming", self.upcoming_command))
        self.application.add_handler(CommandHandler("nowplaying", self.now_playing_command))
        self.application.add_handler(CommandHandler("mylist", self.my_list_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))

        # Callback query handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

        # Message handler
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_search
        ))

        # Inline query handler
        self.application.add_handler(InlineQueryHandler(self.handle_inline_query))

        # Error handler
        self.application.add_error_handler(self.error_handler)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        welcome_text = (
            f"👋 Hello {user.first_name}! Welcome to *Cinephiles Bot*!\n\n"
            "🎬 I'm your personal movie and TV show assistant. Here's what I can do:\n\n"
            "• Search for any movie or TV show\n"
            "• Get trending content\n"
            "• Find upcoming releases\n"
            "• Check what's playing in theaters\n"
            "• Save favorites to your watchlist\n"
            "• Get personalized recommendations\n\n"
            "🔍 You can start by:\n"
            "1. Using the commands below\n"
            "2. Simply typing a movie/show name\n"
            "3. Using inline mode by typing @your_bot_name in any chat\n\n"
            "*Available Commands:*\n"
            "/trending - Get trending movies and shows\n"
            "/upcoming - View upcoming releases\n"
            "/nowplaying - See what's in theaters\n"
            "/mylist - Access your watchlist\n"
            "/settings - Customize your preferences\n"
            "/help - Get detailed help"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔥 Trending", callback_data="menu_trending"),
                InlineKeyboardButton("🎬 Now Playing", callback_data="menu_nowplaying")
            ],
            [
                InlineKeyboardButton("📅 Upcoming", callback_data="menu_upcoming"),
                InlineKeyboardButton("📌 My List", callback_data="menu_mylist")
            ],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
                InlineKeyboardButton("❓ Help", callback_data="menu_help")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send welcome message with animated greeting
        await update.message.reply_text(
            "🎬 *Starting Cinephiles Bot...*",
            parse_mode='Markdown'
        )
        await asyncio.sleep(1)
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

        # Initialize user data
        if not context.user_data.get('initialized'):
            context.user_data['watchlist'] = []
            context.user_data['history'] = []
            context.user_data['preferences'] = {
                'language': 'en',
                'adult_content': False,
                'notifications': True
            }
            context.user_data['initialized'] = True

    async def fetch_tmdb_data(self, endpoint: str, params: dict = None) -> dict:
        if params is None:
            params = {}
        params['api_key'] = TMDB_API_KEY
        
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{TMDB_BASE_URL}{endpoint}", params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"TMDB API error: {response.status} - {await response.text()}")
                        return {"results": []}
        except Exception as e:
            logger.error(f"Error fetching TMDB data: {e}")
            return {"results": []}

    async def format_movie_details(self, movie_data: dict) -> tuple:
        """Format movie details with rich content"""
        title = movie_data.get('title') or movie_data.get('name', 'N/A')
        release_date = movie_data.get('release_date') or movie_data.get('first_air_date', 'N/A')
        rating = movie_data.get('vote_average', 0)
        vote_count = movie_data.get('vote_count', 0)
        genres = [genre['name'] for genre in movie_data.get('genres', [])]
        runtime = movie_data.get('runtime', 'N/A')
        
        # Get credits
        movie_id = movie_data.get('id')
        credits = await self.fetch_tmdb_data(f"/movie/{movie_id}/credits")
        
        # Get director and cast
        director = next((crew['name'] for crew in credits.get('crew', []) 
                        if crew['job'] == 'Director'), 'N/A')
        cast = [actor['name'] for actor in credits.get('cast', [])[:3]]
        
        # Get trailer
        videos = await self.fetch_tmdb_data(f"/movie/{movie_id}/videos")
        trailer = next((video for video in videos.get('results', []) 
                       if video['type'] == 'Trailer'), None)
        
        message = (
            f"🎬 *{title}*\n\n"
            f"📅 Release: {release_date}\n"
            f"⭐ Rating: {rating}/10 ({vote_count:,} votes)\n"
            f"⏱ Runtime: {runtime} minutes\n\n"
            f"🎭 Genres: {', '.join(genres)}\n"
            f"🎥 Director: {director}\n"
            f"👥 Cast: {', '.join(cast)}\n\n"
            f"📝 *Overview:*\n{movie_data.get('overview', 'No overview available.')}\n\n"
        )

        # Create buttons
        buttons = []
        
        # Trailer button if available
        if trailer:
            trailer_url = f"https://www.youtube.com/watch?v={trailer['key']}"
            buttons.append([InlineKeyboardButton("🎥 Watch Trailer", url=trailer_url)])

        # Streaming sources
        watch_buttons = await self.create_source_buttons(str(movie_id), 'movie')
        buttons.append(watch_buttons)

        # Action buttons
        action_buttons = [
            InlineKeyboardButton("👍 Like", callback_data=f"like_{movie_id}"),
            InlineKeyboardButton("📌 Save", callback_data=f"save_{movie_id}"),
            InlineKeyboardButton("📤 Share", callback_data=f"share_{movie_id}")
        ]
        buttons.append(action_buttons)

        # Similar movies button
        buttons.append([InlineKeyboardButton("🔄 Similar Movies", 
                                           callback_data=f"similar_{movie_id}")])

        return message, buttons

    async def create_source_buttons(self, item_id: str, media_type: str, 
                                  season: int = None, episode: int = None) -> List:
        """Create buttons for streaming sources with enhanced options"""
        buttons = []
        
        if media_type == 'movie':
            sources = [
                ("🎬 Source 1", f"https://vidsrc.dev/embed/movie/{item_id}"),
                ("🎬 Source 2", f"https://embed.su/embed/movie/{item_id}"),
                ("🎬 Source 3", f"https://vidsrc.me/embed/movie?imdb={item_id}")
            ]
        else:
            season = season or 1
            episode = episode or 1
            sources = [
                ("📺 Source 1", f"https://vidsrc.dev/embed/tv/{item_id}/{season}/{episode}"),
                ("📺 Source 2", f"https://embed.su/embed/tv/{item_id}/{season}/{episode}"),
                ("📺 Source 3", f"https://vidsrc.me/embed/tv?imdb={item_id}&season={season}&episode={episode}")
            ]

        return [InlineKeyboardButton(label, url=url) for label, url in sources]

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all callback queries with enhanced features"""
        query = update.callback_query
        data = query.data

        try:
            if data.startswith('movie_'):
                await self.show_movie_details(query, context)
            elif data.startswith('tv_'):
                await self.show_tv_details(query, context)
            elif data.startswith('season_'):
                await self.handle_season_selection(query, context)
            elif data.startswith('episode_'):
                await self.handle_episode_selection(query, context)
            elif data.startswith('like_'):
                await self.handle_like(query, context)
            elif data.startswith('save_'):
                await self.handle_save(query, context)
            elif data.startswith('share_'):
                await self.handle_share(query, context)
            elif data.startswith('similar_'):
                await self.show_similar_content(query, context)
            elif data.startswith('menu_'):
                await self.handle_menu_selection(query, context)
            elif data.startswith('settings_'):
                await self.handle_settings_callback(query, context)
        except Exception as e:
            logger.error(f"Error handling callback: {e}")
            await query.answer("An error occurred. Please try again.")

    async def show_movie_details(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed movie information with enhanced formatting"""
        movie_id = query.data.split('_')[1]
        movie_data = await self.fetch_tmdb_data(f"/movie/{movie_id}")
        
        if not movie_data:
            await query.answer("Movie information not available")
            return

        # Get poster image
        poster_path = movie_data.get('poster_path')
        if poster_path:
            poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"
        else:
            poster_url = "https://via.placeholder.com/500x750.png?text=No+Poster+Available"

        message, buttons = await self.format_movie_details(movie_data)
        reply_markup = InlineKeyboardMarkup(buttons)

        try:
            # Send poster image with caption
            await query.message.reply_photo(
                photo=poster_url,
                caption=message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            await query.message.delete()
        except Exception as e:
            logger.error(f"Error sending movie details: {e}")
            await query.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

        # Track user interaction
        if 'history' not in context.user_data:
            context.user_data['history'] = []
        context.user_data['history'].append({
            'movie_id': movie_id,
            'timestamp': datetime.now().isoformat()
        })

    async def show_loading_message(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Message:
        """Show a loading message that can be deleted later"""
        return await context.bot.send_message(
            chat_id=chat_id,
            text="🔄 Loading...",
            parse_mode='Markdown'
        )

    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle search command and queries"""
        query = ' '.join(context.args) if context.args else None
        if not query:
            await update.message.reply_text(
                "Please provide a search term.\nExample: `/search The Matrix`",
                parse_mode='Markdown'
            )
            return

        loading_message = await self.show_loading_message(update.message.chat_id, context)
        
        try:
            results = await self.tmdb.search_multi(query)
            if not results:
                await loading_message.delete()
                await update.message.reply_text(
                    "❌ No results found. Try a different search term.",
                    parse_mode='Markdown'
                )
                return

            await loading_message.delete()
            await self.display_search_results(update, context, results)
        except Exception as e:
            logger.error(f"Search error: {e}")
            await loading_message.delete()
            await update.message.reply_text(
                "❌ An error occurred while searching. Please try again later.",
                parse_mode='Markdown'
            )

    async def show_tv_details(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed TV show information with seasons and episodes"""
        tv_id = query.data.split('_')[1]
        tv_data = await self.fetch_tmdb_data(f"/tv/{tv_id}")
        
        if not tv_data:
            await query.answer("TV show information not available")
            return

        # Get poster and backdrop
        poster_path = tv_data.get('poster_path')
        poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}" if poster_path else None
        
        # Format basic show information
        title = tv_data.get('name', 'N/A')
        first_air_date = tv_data.get('first_air_date', 'N/A')
        rating = tv_data.get('vote_average', 0)
        seasons_count = tv_data.get('number_of_seasons', 0)
        episodes_count = tv_data.get('number_of_episodes', 0)
        genres = [genre['name'] for genre in tv_data.get('genres', [])]
        
        message = (
            f"📺 *{title}*\n\n"
            f"📅 First Aired: {first_air_date}\n"
            f"⭐ Rating: {rating}/10\n"
            f"🎬 Seasons: {seasons_count}\n"
            f"episodes: {episodes_count}\n"
            f"🎭 Genres: {', '.join(genres)}\n\n"
            f"📝 *Overview:*\n{tv_data.get('overview', 'No overview available.')}\n\n"
            f"Select a season to view episodes:"
        )

        # Create season selection buttons
        keyboard = []
        for season in range(1, seasons_count + 1):
            keyboard.append([
                InlineKeyboardButton(
                    f"Season {season}",
                    callback_data=f"season_{tv_id}_{season}"
                )
            ])

        # Add action buttons
        action_buttons = [
            InlineKeyboardButton("👍 Like", callback_data=f"like_tv_{tv_id}"),
            InlineKeyboardButton("📌 Save", callback_data=f"save_tv_{tv_id}"),
            InlineKeyboardButton("📤 Share", callback_data=f"share_tv_{tv_id}")
        ]
        keyboard.append(action_buttons)
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if poster_url:
                await query.message.reply_photo(
                    photo=poster_url,
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                await query.message.delete()
            else:
                await query.message.edit_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Error sending TV show details: {e}")
            await query.answer("Error displaying TV show details")

    async def handle_season_selection(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle season selection for TV series"""
        try:
            # Parse the callback data
            _, series_id, season_number = query.data.split('_')
            
            # Fetch season details from TMDB
            season = await self.tmdb.get_tv_season(series_id, season_number)
            if not season:
                await query.answer("Season information not available!")
                return

            # Create episode list buttons
            keyboard = []
            for episode in season.get('episodes', []):
                episode_number = episode.get('episode_number')
                episode_name = episode.get('name')
                keyboard.append([
                    InlineKeyboardButton(
                        f"Episode {episode_number}: {episode_name}",
                        callback_data=f"episode_{series_id}_{season_number}_{episode_number}"
                    )
                ])

            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back to Seasons", callback_data=f"series_{series_id}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Get season poster or use series poster as fallback
            poster_path = season.get('poster_path')
            if poster_path:
                photo_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
            else:
                # Fetch series details for fallback poster
                series = await self.tmdb.get_tv_details(series_id)
                photo_url = f"https://image.tmdb.org/t/p/w500{series.get('poster_path')}"

            # Create season overview text
            season_text = f"*Season {season_number}*\n\n"
            season_text += f"*Air Date:* {season.get('air_date', 'N/A')}\n"
            season_text += f"*Episodes:* {len(season.get('episodes', []))}\n\n"
            season_text += season.get('overview', 'No overview available.')

            # Delete the previous message and send a new one
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo_url,
                caption=season_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            await query.answer()

        except Exception as e:
            logger.error(f"Error in season selection: {e}")
            await query.answer("An error occurred while fetching season information.")

    async def handle_episode_selection(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle episode selection"""
        try:
            # Parse callback data
            _, series_id, season_number, episode_number = query.data.split('_')
            
            # Fetch episode details
            episode = await self.tmdb.get_tv_episode(series_id, season_number, episode_number)
            if not episode:
                await query.answer("Episode information not available!")
                return

            # Create episode details text
            episode_text = f"*{episode.get('name')}*\n"
            episode_text += f"Season {season_number}, Episode {episode_number}\n\n"
            episode_text += f"*Air Date:* {episode.get('air_date', 'N/A')}\n"
            episode_text += f"*Rating:* {episode.get('vote_average', 'N/A')}/10\n\n"
            episode_text += episode.get('overview', 'No overview available.')

            # Create keyboard with back button
            keyboard = [[
                InlineKeyboardButton(
                    "🔙 Back to Season", 
                    callback_data=f"season_{series_id}_{season_number}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Get episode still or use season poster as fallback
            still_path = episode.get('still_path')
            if still_path:
                photo_url = f"https://image.tmdb.org/t/p/w500{still_path}"
            else:
                # Fetch series details for fallback poster
                series = await self.tmdb.get_tv_details(series_id)
                photo_url = f"https://image.tmdb.org/t/p/w500{series.get('poster_path')}"

            # Delete previous message and send new one
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo_url,
                caption=episode_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            await query.answer()

        except Exception as e:
            logger.error(f"Error in episode selection: {e}")
            await query.answer("An error occurred while fetching episode information.")

    async def handle_like(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle like button interactions"""
        content_id = query.data.split('_')[1]
        
        if 'liked_content' not in context.user_data:
            context.user_data['liked_content'] = set()
            
        if content_id in context.user_data['liked_content']:
            context.user_data['liked_content'].remove(content_id)
            await query.answer("Removed from liked content!")
        else:
            context.user_data['liked_content'].add(content_id)
            await query.answer("Added to liked content!")

    async def handle_save(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle save to watchlist functionality"""
        content_id = query.data.split('_')[1]
        
        if 'watchlist' not in context.user_data:
            context.user_data['watchlist'] = set()
            
        if content_id in context.user_data['watchlist']:
            context.user_data['watchlist'].remove(content_id)
            await query.answer("Removed from watchlist!")
        else:
            context.user_data['watchlist'].add(content_id)
            await query.answer("Added to watchlist!")

    async def web_app(self):
        """Create web application with health check and metrics"""
        app = web.Application()
        app.router.add_get('/', self.handle_root)
        app.router.add_get('/health', self.handle_health)
        app.router.add_get('/metrics', self.handle_metrics)
        return app

    async def handle_root(self, _):
        """Handle root endpoint"""
        return web.Response(text="Cinephiles Bot is running!")

    async def handle_health(self, request):
        """Handle health check endpoint"""
        return web.Response(text="OK", status=200)

    async def handle_metrics(self, request):
        """Handle metrics endpoint"""
        metrics = {
            "users": len(self.user_data),
            "uptime": str(datetime.now() - self.start_time),
            "status": "healthy"
        }
        return web.Response(
            text=json.dumps(metrics),
            content_type='application/json'
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send help message when command /help is issued."""
        help_text = (
            "*Available Commands:*\n\n"
            "/start - Start the bot\n"
            "/trending - Get trending movies and shows\n"
            "/upcoming - View upcoming releases\n"
            "/nowplaying - See what's in theaters\n"
            "/mylist - Access your watchlist\n"
            "/settings - Customize your preferences\n"
            "/help - Show this help message\n\n"
            "You can also search for any movie or TV show by simply typing its name!"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def trending_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trending command"""
        trending_data = await self.fetch_tmdb_data("/trending/all/day")
        if not trending_data.get('results'):
            await update.message.reply_text("Unable to fetch trending content.")
            return
        
        message = "🔥 *Trending Today:*\n\n"
        keyboard = []
        for item in trending_data['results'][:8]:
            title = item.get('title') or item.get('name')
            media_type = item.get('media_type')
            keyboard.append([InlineKeyboardButton(
                f"{title}", 
                callback_data=f"{media_type}_{item['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    async def upcoming_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /upcoming command"""
        upcoming_data = await self.fetch_tmdb_data("/movie/upcoming")
        if not upcoming_data.get('results'):
            await update.message.reply_text("Unable to fetch upcoming movies.")
            return
        
        message = "📅 *Upcoming Movies:*\n\n"
        keyboard = []
        for movie in upcoming_data['results'][:8]:
            keyboard.append([InlineKeyboardButton(
                f"{movie['title']}", 
                callback_data=f"movie_{movie['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    async def now_playing_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /nowplaying command"""
        now_playing = await self.fetch_tmdb_data("/movie/now_playing")
        if not now_playing.get('results'):
            await update.message.reply_text("Unable to fetch movies in theaters.")
            return
        
        message = "🎬 *Now Playing in Theaters:*\n\n"
        keyboard = []
        for movie in now_playing['results'][:8]:
            keyboard.append([InlineKeyboardButton(
                f"{movie['title']}", 
                callback_data=f"movie_{movie['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    async def my_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /mylist command"""
        if not context.user_data.get('watchlist'):
            await update.message.reply_text("Your watchlist is empty!")
            return
        
        message = "📌 *Your Watchlist:*\n\n"
        keyboard = []
        for item_id in context.user_data['watchlist']:
            # Fetch item details from TMDB
            item_data = await self.fetch_tmdb_data(f"/movie/{item_id}")
            if item_data:
                keyboard.append([InlineKeyboardButton(
                    f"{item_data['title']}", 
                    callback_data=f"movie_{item_id}"
                )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        # Get the user ID from either Update or CallbackQuery
        user_id = str(update.effective_user.id if isinstance(update, Update) else update.from_user.id)
        
        # Get current settings from data manager
        settings = self.data_manager.get_settings(user_id)
        
        # Create settings keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    f" Language: {settings.get('language', 'en').upper()}", 
                    callback_data="settings_language"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔞 Adult Content: {'✅' if settings.get('adult_content', False) else '❌'}", 
                    callback_data="settings_adult"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔔 Notifications: {'✅' if settings.get('notifications', True) else '❌'}", 
                    callback_data="settings_notifications"
                )
            ],
            [
                InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "⚙️ *Settings*\nCustomize your movie bot experience:"
        
        if isinstance(update, Update):
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_settings_callback(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle settings-related callbacks"""
        user_id = str(query.from_user.id)
        settings = self.data_manager.get_settings(user_id)
        setting_type = query.data.split('_')[1]

        if setting_type == 'language':
            # Toggle between 'en' and 'es' (you can add more languages)
            current_lang = settings.get('language', 'en')
            settings['language'] = 'es' if current_lang == 'en' else 'en'
            
        elif setting_type == 'adult':
            # Toggle adult content
            settings['adult_content'] = not settings.get('adult_content', False)
            
        elif setting_type == 'notifications':
            # Toggle notifications
            settings['notifications'] = not settings.get('notifications', True)

        # Save updated settings
        self.data_manager.update_settings(user_id, settings)
        
        # Show updated settings menu
        await self.settings_command(query, context)
        await query.answer(f"Setting updated!")

    async def handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries for movie/show searches"""
        query = update.inline_query.query

        if not query:
            return

        results = []
        search_results = await self.fetch_tmdb_data(
            "/search/multi",
            {"query": query, "page": 1}
        )

        for item in search_results.get('results', [])[:10]:
            media_type = item.get('media_type', 'movie')
            title = item.get('title') or item.get('name', 'Unknown')
            year = (item.get('release_date') or item.get('first_air_date', ''))[:4]
            overview = item.get('overview', 'No overview available')
            
            # Create result description
            description = f"{media_type.upper()} ({year})\n{overview[:150]}..."
            
            results.append(
                InlineQueryResultArticle(
                    id=str(item['id']),
                    title=title,
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎬 *{title}* ({year})\n\n{overview}",
                        parse_mode='Markdown'
                    ),
                    thumb_url=f"{TMDB_IMAGE_BASE_URL}{item.get('poster_path', '')}"
                )
            )

        await update.inline_query.answer(results)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors in the bot"""
        logger.error(f"Update {update} caused error {context.error}")
        
        error_message = "An error occurred while processing your request. Please try again later."
        
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    error_message,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

    async def handle_menu_selection(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
        """Handle main menu button selections"""
        menu_type = query.data.split('_')[1]

        try:
            if menu_type == 'trending':
                await self.trending_command(query, context)
            elif menu_type == 'nowplaying':
                await self.now_playing_command(query, context)
            elif menu_type == 'upcoming':
                await self.upcoming_command(query, context)
            elif menu_type == 'mylist':
                await self.my_list_command(query, context)
            elif menu_type == 'settings':
                await self.settings_command(query, context)
            elif menu_type == 'help':
                await self.help_command(query, context)
            elif menu_type == 'main':
                # Return to main menu
                await self.start_command(query, context)
            
            await query.answer()
        except Exception as e:
            logger.error(f"Error in menu selection: {e}")
            await query.answer("An error occurred. Please try again.")

    async def format_content_details(self, content: dict, content_type: str) -> str:
        """Format content details with rich information"""
        text = f"*{content.get('title', content.get('name', 'N/A')}*\n\n"
        
        # Release date/year
        release_date = content.get('release_date', content.get('first_air_date', 'N/A'))
        if release_date and release_date != 'N/A':
            year = release_date.split('-')[0]
            text += f"📅 *Year:* {year}\n"

        # Rating
        rating = content.get('vote_average', 0)
        vote_count = content.get('vote_count', 0)
        if rating and vote_count:
            stars = '⭐' * round(rating/2)
            text += f"*Rating:* {rating}/10 {stars} ({vote_count:,} votes)\n"

        # Genres
        if content.get('genres'):
            genres = ', '.join([g['name'] for g in content['genres']])
            text += f"🎭 *Genres:* {genres}\n"

        # Runtime/Episodes
        if content_type == 'movie' and content.get('runtime'):
            text += f"⏱️ *Runtime:* {content['runtime']} minutes\n"
        elif content_type == 'tv':
            seasons = content.get('number_of_seasons', 'N/A')
            episodes = content.get('number_of_episodes', 'N/A')
            text += f"📺 *Seasons:* {seasons} | *Episodes:* {episodes}\n"

        # Overview
        if content.get('overview'):
            text += f"\n📝 *Overview:*\n{content['overview']}\n"

        # Additional info
        if content.get('status'):
            text += f"\n📊 *Status:* {content['status']}\n"

        return text

    async def cleanup_sessions(self, context: ContextTypes.DEFAULT_TYPE):
        """Periodic cleanup of old sessions"""
        self.session_manager.cleanup_old_sessions()

    async def handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generic command handler with rate limiting"""
        user_id = update.effective_user.id
        
        if not self.rate_limiter.can_proceed(user_id):
            await update.message.reply_text(
                "⚠️ You're making too many requests. Please wait a moment.",
                parse_mode='Markdown'
            )
            return False
        
        self.session_manager.update_activity(user_id)
        return True

    def run(self):
        """Run the bot and web server"""
        self.start_time = datetime.now()
        
        async def start():
            # Start web server
            runner = web.AppRunner(await self.web_app())
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', PORT)
            await site.start()
            
            logger.info(f"Web server started on port {PORT}")
            
            # Start bot with polling
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            # Keep the application running indefinitely
            while True:
                await asyncio.sleep(1)

        # Run the async function
        try:
            asyncio.run(start())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Error starting application: {e}")

if __name__ == '__main__':
    bot = MovieBot()
    bot.run()   
