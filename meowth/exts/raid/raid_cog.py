from meowth import Cog, command, bot, checks
from meowth.exts.map import Gym, ReportChannel
from meowth.exts.pkmn import Pokemon, Move
from meowth.exts.weather import Weather
from meowth.exts.want import Want
from meowth.utils import formatters
from meowth.utils.converters import Message
from . import raid_info
from . import raid_checks

from math import ceil
import discord
import asyncio
import aiohttp
import time
from datetime import timezone, datetime

class RaidBoss(Pokemon):

    def __init__(self, pkmn):
        self.bot = pkmn.bot
        self.id = pkmn.id
        self.form = pkmn.form
        self.shiny = False
        self.gender = None
        self.quickMoveid = pkmn.quickMoveid
        self.chargeMoveid = pkmn.chargeMoveid

    

    @property
    def raid_level(self):
        for level in self.bot.raid_info.raid_lists:
            if self.id in self.bot.raid_info.raid_lists[level]:
                return level
        
    
    @classmethod
    async def convert(cls, ctx, arg):
        pkmn = await Pokemon.convert(ctx, arg)
        return cls(pkmn)   

class Raid():

    def __init__(self, bot, gym: Gym=None, level=None,
        pkmn: RaidBoss=None, hatch: float=None, end: float=None,
        trainer_dict: dict={}):
        self.bot = bot
        self.gym = gym
        self.level = level
        self.pkmn = pkmn
        if hatch:
            self.hatch = hatch
            active_time = bot.raid_info.raid_times[level][1]
            self.end = hatch + active_time*60
        elif end:
            self.end = end
            self.hatch = hatch
        self.trainer_dict = trainer_dict

    @property
    def max_hatch(self):
        level = self.level
        max_times = self.bot.raid_info.raid_times[level]
        return max_times[0]
    
    @property
    def max_active(self):
        level = self.level
        max_times = self.bot.raid_info.raid_times[level]
        return max_times[1]
    
    def update_time(self, new_time: int):
        if new_time < 0:
            raise
        level = self.level
        max_times = self.bot.raid_info.raid_times[level]
        max_hatch = max_times[0]
        max_active = max_times[1]
        if self.hatch >= time.time():
            if new_time > max_hatch:
                raise
            else:
                self.hatch = time.time() + new_time*60
                self.end = self.hatch + max_active*60
        else:
            self.end = time.time() + new_time*60
    
    @property
    def boss_list(self):
        level = self.level
        boss_list = self.bot.raid_info.raid_lists[level]
        return boss_list
    
    @property
    def pokebattler_url(self):
        pkmnid = self.pkmn.id
        url = f"https://www.pokebattler.com/raids/{pkmnid}"
        return url
        
    @staticmethod
    def pokebattler_data_url(pkmnid, level, weather):
        json_url = 'https://fight.pokebattler.com/raids/defenders/'
        json_url += f"{pkmnid}/levels/RAID_LEVEL_{level}/attackers/levels/"
        json_url += "30/strategies/CINEMATIC_ATTACK_WHEN_POSSIBLE/"
        json_url += f"DEFENSE_RANDOM_MC"
        json_url += f"?sort=OVERALL&weatherCondition={weather}"
        json_url += "&dodgeStrategy=DODGE_REACTION_TIME"
        json_url += "&aggregation=AVERAGE&randomAssistants=-1"
        return json_url
    
    
    async def channel_name(self):
        gym_name = await self.gym._name()
        if self.pkmn:
            boss_name = await self.pkmn.name()
            return f"{boss_name}-{gym_name}"
        else:
            return f"{self.level}-{gym_name}"
    
    async def on_raw_reaction_add(self, payload):
        id_string = f"{payload.channel_id}/{payload.message_id}"
        if id_string not in self.message_ids:
            return
    
    async def monitor_status(self):
        while True:
            hatch = self.hatch
            end = self.end
            if hatch:
                sleeptime = time.time() - hatch
                await asyncio.sleep(sleeptime)
                hatch = self.hatch
                if hatch <= time.time():
                    await self.hatch_egg()
                    continue
                else:
                    continue
            else:
                sleeptime = time.time() - end
                await asyncio.sleep(sleeptime)
                end = self.end
                if end <= time.time():
                    await self.expire_raid()
                else:
                    continue
        

    
    async def weather(self):
        gym = self.gym
        weather = await gym.weather()
        return weather
    
    async def is_boosted(self):
        weather = await self.weather()
        pkmn = self.pkmn
        return await pkmn.is_boosted(weather)
    
    async def cp_range(self):
        boost = await self.is_boosted()
        if boost:
            self.pkmn.lvl = 25
        else:
            self.pkmn.lvl = 20
        self.pkmn.attiv = 10
        self.pkmn.defiv = 10
        self.pkmn.staiv = 10
        low_cp = await self.pkmn.calculate_cp()
        self.pkmn.attiv = 15
        self.pkmn.defiv = 15
        self.pkmn.staiv = 15
        high_cp = await self.pkmn.calculate_cp()
        return [low_cp, high_cp]
    
    async def generic_counters_data(self):
        data_table = self.bot.dbi.table('counters_data')
        weather = await self.weather()
        boss = self.pkmn
        boss_id = boss.id
        level = self.level
        if boss.quickMoveid:
            fast_move_id = boss.quickMoveid
        else:
            fast_move_id = 'random'
        if boss.chargeMoveid:
            charge_move_id = boss.chargeMoveid
        else:
            charge_move_id = 'random'
        query = data_table.query().select().where(
            boss_id=boss_id, weather=weather, level=level,
            fast_move=fast_move_id, charge_move=charge_move_id)
        query_dict = (await query.get())[0]
        ctrs_list = []
        for x in range(1,7):
            ctrid = query_dict[f'counter_{x}_id']
            ctrfast = query_dict[f'counter_{x}_fast']
            ctrcharge = query_dict[f'counter_{x}_charge']
            ctr = Pokemon(self.bot, ctrid, quickMoveid=ctrfast, chargeMoveid=ctrcharge)
            ctrs_list.append(ctr)
        return ctrs_list

    async def egg_embed(self):
        raid_icon = 'https://media.discordapp.net/attachments/423492585542385664/512682888236367872/imageedit_1_9330029197.png'
        footer_icon = 'https://media.discordapp.net/attachments/346766728132427777/512699022822080512/imageedit_10_6071805149.png'
        level = self.level
        egg_img_url = self.bot.raid_info.egg_images[level]
        # color = await formatters.url_color(egg_img_url)
        boss_list = self.boss_list
        hatch = self.hatch
        hatchdt = datetime.fromtimestamp(hatch)
        gym = self.gym
        directions_url = await gym.url()
        directions_text = await gym._name()
        exraid = await gym._exraid()
        if exraid:
            directions_text += " (EX Raid Gym)"
        weather = await self.weather()
        weather = Weather(self.bot, weather)
        weather_name = await weather.name()
        weather_emoji = await weather.boosted_emoji_str()
        boss_names = []
        for i in range(len(boss_list)):
            x = boss_list[i]
            boss = RaidBoss(Pokemon(self.bot, x))
            name = f'{i+1}\u20e3'
            name += await boss.name()
            type_emoji = await boss.type_emoji()
            shiny_available = await boss._shiny_available()
            if shiny_available:
                name += ':sparkles:'
            boss_names.append(f"{name} {type_emoji}")
        half_length = ceil(len(boss_names)/2)
        bosses_left = boss_names[:(half_length)]
        bosses_right = boss_names[(half_length):]
        fields = {
            "Weather": (False, f"{weather_name} {weather_emoji}"),
            "Possible Bosses:": "\n".join(bosses_left),
            "\u200b": "\n".join(bosses_right)
        }
        footer_text = "Hatching"
        embed = formatters.make_embed(icon=raid_icon, title=directions_text,
            thumbnail=egg_img_url, title_url=directions_url, # msg_colour=color,
            fields=fields, footer=footer_text, footer_icon=footer_icon)
        embed.timestamp = hatchdt
        return embed
    
    async def hatched_embed(self):
        raid_icon = 'https://media.discordapp.net/attachments/423492585542385664/512682888236367872/imageedit_1_9330029197.png'
        footer_icon = 'https://media.discordapp.net/attachments/346766728132427777/512699022822080512/imageedit_10_6071805149.png'
        level = self.level
        egg_img_url = self.bot.raid_info.egg_images[level]
        # color = await formatters.url_color(egg_img_url)
        boss_list = self.boss_list
        end = self.end
        enddt = datetime.fromtimestamp(end)
        gym = self.gym
        directions_url = await gym.url()
        directions_text = await gym._name()
        exraid = await gym._exraid()
        if exraid:
            directions_text += " (EX Raid Gym)"
        weather = await self.weather()
        weather = Weather(self.bot, weather)
        weather_name = await weather.name()
        weather_emoji = await weather.boosted_emoji_str()
        boss_names = []
        for i in range(len(boss_list)):
            x = boss_list[i]
            boss = RaidBoss(Pokemon(self.bot, x))
            name = await boss.name()
            type_emoji = await boss.type_emoji()
            shiny_available = await boss._shiny_available()
            if shiny_available:
                name += ':sparkles:'
            boss_names.append(f"{name} {type_emoji}")
        length = len(boss_list)
        react_list = formatters.mc_emoji(length)
        choice_list = [react_list[i] + ' ' + boss_names[i] for i in range(len(react_list))]
        print(choice_list)
        half_length = ceil(len(boss_names)/2)
        bosses_left = choice_list[:(half_length)]
        print(bosses_left)
        bosses_right = choice_list[(half_length):]
        print(bosses_right)
        fields = {
            "Weather": (False, f"{weather_name} {weather_emoji}"),
            "Possible Bosses:": "\n".join(bosses_left),
            "\u200b": "\n".join(bosses_right)
        }
        footer_text = "Ending"
        embed = formatters.make_embed(icon=raid_icon, title=directions_text,
            thumbnail=egg_img_url, title_url=directions_url, # msg_colour=color,
            fields=fields, footer=footer_text, footer_icon=footer_icon)
        embed.timestamp = enddt
        return embed

    async def raid_embed(self):
        raid_icon = 'https://media.discordapp.net/attachments/423492585542385664/512682888236367872/imageedit_1_9330029197.png' #TODO
        footer_icon = 'https://media.discordapp.net/attachments/346766728132427777/512699022822080512/imageedit_10_6071805149.png'
        boss = self.pkmn
        level = boss.raid_level
        if level == 6:
            display_level = 5
        else:
            display_level = level
        boss_name = await boss.name()
        type_emoji = await boss.type_emoji()
        shiny_available = await boss._shiny_available()
        if shiny_available:
            boss_name += ':sparkles:'
        boss_type = await boss.type_emoji()
        quick_move = Move(self.bot, boss.quickMoveid) if boss.quickMoveid else None
        charge_move = Move(self.bot, boss.chargeMoveid) if boss.chargeMoveid else None
        if quick_move:
            quick_name = await quick_move.name()
            quick_emoji = await quick_move.emoji()
        else:
            quick_name = "Unknown"
            quick_emoji = ""
        if charge_move:
            charge_name = await charge_move.name()
            charge_emoji = await charge_move.emoji()
        else:
            charge_name = "Unknown"
            charge_emoji = ""
        moveset = f"{quick_name} {quick_emoji} | {charge_name} {charge_emoji}"
        weather = await self.weather()
        weather = Weather(self.bot, weather)
        weather_name = await weather.name()
        weather_emoji = await weather.boosted_emoji_str()
        is_boosted = await boss.is_boosted(weather.value)
        cp_range = await self.cp_range()
        cp_str = f"{cp_range[0]}-{cp_range[1]}"
        end = self.end
        enddt = datetime.fromtimestamp(end)
        if is_boosted:
            cp_str += " (Boosted)"
        img_url = await boss.sprite_url()
        # color = await boss.color()
        gym = self.gym
        directions_url = await gym.url()
        gym_name = await gym._name()
        exraid = await gym._exraid()
        directions_text = gym_name
        if exraid:
            directions_text += " (EX Raid Gym)"
        resists = await boss.resistances_emoji()
        weaks = await boss.weaknesses_emoji()
        ctrs_list = await self.generic_counters_data()
        await self.get_trainer_dict()
        status_dict = self.status_dict
        status_str = f"{self.bot.config.emoji['maybe']}: {status_dict['maybe']} | "
        status_str += f"{self.bot.config.emoji['coming']}: {status_dict['coming']} | "
        status_str += f"{self.bot.config.emoji['here']}: {status_dict['here']}"
        team_dict = self.team_dict
        team_str = f"{self.bot.config.team_emoji['mystic']}: {team_dict['mystic']} | "
        team_str += f"{self.bot.config.team_emoji['instinct']}: {team_dict['instinct']} | "
        team_str += f"{self.bot.config.team_emoji['valor']}: {team_dict['valor']} | "
        team_str += f"{self.bot.config.team_emoji['unknown']}: {team_dict['unknown']}"
        fields = {
            "Boss": f"{boss_name} {type_emoji}",
            "Weather": f"{weather_name} {weather_emoji}",
            "Weaknesses": weaks,
            "Resistances": resists,
            "CP Range": f"{cp_range[0]}-{cp_range[1]}",
            "Moveset": moveset,
            "Status List": status_str,
            "Team List": team_str
        }
        i = 1
        ctrs_str = []
        for ctr in ctrs_list:
            name = await ctr.name()
            fast = Move(self.bot, ctr.quickMoveid)
            fast_name = await fast.name()
            fast_emoji = await fast.emoji()
            charge = Move(self.bot, ctr.chargeMoveid)
            charge_name = await charge.name()
            charge_emoji = await charge.emoji()
            ctr_str = f"**{name}**: {fast_name} {fast_emoji} | {charge_name} {charge_emoji}"
            ctrs_str.append(ctr_str)
            i += 1
        ctrs_str.append(f'[Results courtesy of Pokebattler](https://www.pokebattler.com/raids/{boss.id})')
        fields['<:pkbtlr:512707623812857871> Counters'] = "\n".join(ctrs_str)
        embed = formatters.make_embed(icon=raid_icon, title=directions_text, # msg_colour=color,
            title_url=directions_url, thumbnail=img_url, fields=fields, footer="Ending",
            footer_icon=footer_icon)
        embed.timestamp = enddt
        return embed
    
    async def update_messages(self, content=''):
        msg_list = []
        message_ids = self.message_ids
        if self.hatch:
            embed = await self.egg_embed()
        elif self.pkmn is None:
            embed = await self.hatched_embed()
        else:
            embed = await self.raid_embed()
        for messageid in message_ids:
            msg = await Message.from_id_string(self.bot, messageid)
            await msg.edit(content=content, embed=embed)
            msg_list.append(msg)
        if self.channel_ids:
            for chanid in self.channel_ids:
                channel = self.bot.get_channel(chanid)
                msg = await channel.send(content, embed=embed)
                msg_list.append(msg)
        return msg_list


    
    async def hatch_egg(self):
        content = "This raid egg has hatched! React below to report the boss!"
        self.hatch = None
        boss_list = self.boss_list
        length = len(boss_list)
        react_list = formatters.mc_emoji(length)
        boss_dict = dict(zip(react_list, boss_list))
        msg_list = await self.update_messages(content=content)
        response = await formatters.ask(self.bot, msg_list, timeout=(self.end-time.time()),
            react_list=react_list)
        if response:
            emoji = str(response.emoji)
            pkmn = boss_dict[emoji]
            return await self.report_hatch(pkmn)
        

    async def report_hatch(self, pkmn):
        self.pkmn = RaidBoss(Pokemon(self.bot, pkmn))
        return await self.update_messages()

    # async def update_weather(self, weather):
    
    # async def expire_raid(self):
    
    # async def update_gym(self, gym):

    async def rsvp(self, user, status, bosses: list=None, total: int=1,
        bluecount: int=0, yellowcount: int=0, redcount: int=0):
        if any((bluecount, yellowcount, redcount)):
            calctotal = sum(bluecount, yellowcount, redcount)
            if not total or total < calctotal:
                total = calctotal
                unknowncount = 0
            elif total >= calctotal:
                unknowncount = total - calctotal
        d = {
            'status': status,
            'bosses': bosses,
            'total': total,
            'bluecount': bluecount,
            'yellowcount': yellowcount,
            'redcount': redcount,
            'unknowncount': unknowncount
        }
        self.trainer_dict[user] = d
        await self.update_messages()

    @property
    def boss_interest_dict(self):
        boss_list = self.boss_list
        d = {x: 0 for x in boss_list}
        trainer_dict = self.trainer_dict
        for trainer in trainer_dict:
            total = trainer_dict[trainer]['total']
            bosses = trainer_dict[trainer]['bosses']
            for boss in bosses:
                d[boss] += total
        return d

    @property
    def status_dict(self):
        d = {
            'interested': 0,
            'coming': 0,
            'here': 0,
            'lobby': 0
        }
        trainer_dict = self.trainer_dict
        for trainer in trainer_dict:
            total = trainer_dict[trainer]['total']
            status = trainer_dict[trainer]['status']
            d[status] += total
        return d
    
    @property
    def team_dict(self):
        d = {
            'mystic': 0,
            'instinct': 0,
            'valor': 0,
            'unknown': 0
        }
        trainer_dict = self.trainer_dict
        for trainer in trainer_dict:
            bluecount = trainer_dict[trainer]['bluecount']
            yellowcount = trainer_dict[trainer]['yellowcount']
            redcount = trainer_dict[trainer]['redcount']
            unknowncount = trainer_dict[trainer]['unknowncount']
            d['mystic'] += bluecount
            d['instinct'] += yellowcount
            d['valor'] += redcount
            d['unknown'] += unknowncount
        return d

    async def get_trainer_dict(self):
        def data(rcrd):
            trainer = rcrd['id']
            bosses = rcrd.get('bosses')
            total = rcrd['total']
            bluecount = rcrd.get('bluecount')
            yellowcount = rcrd.get('yellowcount')
            redcount = rcrd.get('redcount')
            unknowncount = rcrd.get('unknowncount')
            rcrd_dict = {
                'bosses': bosses,
                'total': total,
                'bluecount': bluecount,
                'yellowcount': yellowcount,
                'redcount': redcount,
                'unknowncount': unknowncount
            }
            return trainer, rcrd_dict
        trainer_dict = {}
        user_table = self.bot.dbi.table('users')
        int_query = user_table.query()
        int_query.where(user_table['interested_list'].contains_(self.id))
        int_data = await int_query.get()
        for rcrd in int_data:
            trainer, rcrd_dict = data(rcrd)
            rcrd_dict['status'] = 'interested'
            trainer_dict[trainer] = rcrd_dict
        com_query = user_table.query()
        com_query.where(coming=self.id)
        com_data = await com_query.get()
        for rcrd in com_data:
            trainer, rcrd_dict = data(rcrd)
            rcrd_dict['status'] = 'coming'
            trainer_dict[trainer] = rcrd_dict
        here_query = user_table.query()
        here_query.where(here=self.id)
        here_data = await here_query.get()
        for rcrd in here_data:
            trainer, rcrd_dict = data(rcrd)
            rcrd_dict['status'] = 'here'
            trainer_dict[trainer] = rcrd_dict
        lob_query = user_table.query()
        lob_query.where(lobby=self.id)
        lob_data = await lob_query.get()
        for rcrd in lob_data:
            trainer, rcrd_dict = data(rcrd)
            rcrd_dict['status'] = 'lobby'
            trainer_dict[trainer] = rcrd_dict
        self.trainer_dict = trainer_dict
        return trainer_dict


    @classmethod
    async def from_data(cls, bot, data):
        gym = Gym(bot, data['gym'])
        level = data['level']
        pkmnid, quick, charge = data.get('pkmn', (None, None, None))
        if pkmnid:
            pkmn = Pokemon(bot, pkmnid, quickMoveid=quick, chargeMoveid=charge)
            boss = RaidBoss(pkmn)
        else:
            boss = None
        hatch = data.get('hatch')
        end = data['endtime']
        raid = cls(bot, gym, level=level, pkmn=boss, hatch=hatch, end=end)
        raid.channel_ids = data.get('channels')
        raid.message_ids = data.get('messages')
        raid.id = data['id']
        await raid.get_trainer_dict()
        bot.add_listener(raid.on_raw_reaction_add)
        return raid  
        

class RaidCog(Cog):
    
    def __init__(self, bot):
        bot.raid_info = raid_info
        self.bot = bot

    
    @command()
    @raid_checks.raid_enabled()
    async def raid(self, ctx, level_or_boss, gym: Gym, endtime: int):
        raid_table = ctx.bot.dbi.table('raids')
        query = raid_table.query()
        query.where(gym=gym.id, guild=ctx.guild.id)
        old_raid = await query.get()
        if old_raid:
            old_raid = old_raid[0]
            old_raid = await Raid.from_data(ctx.bot, old_raid)
            if old_raid.hatch:
                embed = await old_raid.egg_embed()
            else:
                embed = await old_raid.raid_embed()
            if old_raid.channel_ids:
                mentions = []
                for channelid in old_raid.channel_ids:
                    channel = ctx.guild.get_channel(channelid)
                    mention = channel.mention
                    mentions.append(mention)
                return await ctx.send(f"""There is already a raid reported at this gym! Coordinate here: {", ".join(mentions)}""", embed=embed)
            else:
                msg = await ctx.send(f"""There is already a raid reported at this gym! Coordinate here!""", embed=embed)
                old_raid.message_ids.append(f"{msg.channel.id}/{msg.id}")
                return msg
        if level_or_boss.isdigit():
            level = level_or_boss
            want = Want(ctx.bot, level, ctx.guild.id)
            role = await want.role()
            boss = None
            hatch = time.time() + 60*endtime
            end = hatch + 60*ctx.bot.raid_info.raid_times[level][1]
        else:
            boss = await RaidBoss.convert(ctx, level_or_boss)
            want = Want(ctx.bot, boss.id, ctx.guild.id)
            role = await want.role()
            level = boss.raid_level
            end = time.time() + 60*endtime
            hatch = None
        new_raid = Raid(ctx.bot, gym, level=level, pkmn=boss, hatch=hatch, end=end)
        new_raid.channel_ids = []
        new_raid.message_ids = []
        if new_raid.hatch:
            embed = await new_raid.egg_embed()
        else:
            embed = await new_raid.raid_embed()
        if role:
            reportcontent = role.mention + " - "
        else:
            reportcontent = ""
        raid_mode = await raid_checks.raid_category(ctx, level)
        if raid_mode == 'message':
            reportcontent += "Coordinate this raid here using the reactions below!"
            if not role:
                dm_content = f"Coordinate this raid in {ctx.channel.name}!"
                dms = await want.notify_users(dm_content, embed)
                new_raid.message_ids.extend(dms)
            reportmsg = await ctx.send(reportcontent, embed=embed)
            new_raid.message_ids.append(f"{reportmsg.channel.id}/{reportmsg.id}")
        elif raid_mode.isdigit():
            catid = int(raid_mode)
            if catid:
                category = ctx.guild.get_channel(catid)
            else:
                category = None
            raid_channel_name = await new_raid.channel_name()
            raid_channel = await ctx.guild.create_text_channel(raid_channel_name,
                category=category)
            new_raid.channel_ids.append(raid_channel.id)
            reportcontent += f"Coordinate this raid in {raid_channel.mention}!"
            if not role:
                dm_content = f"Coordinate this raid in {raid_channel.name}!"
                dms = await want.notify_users(dm_content, embed)
                new_raid.message_ids.extend(dms)
            reportmsg = await ctx.send(reportcontent, embed=embed)
            new_raid.message_ids.extend(f"{reportmsg.channel.id}/{reportmsg.id}")
        insert = raid_table.insert()
        data = {
            'gym': gym.id,
            'guild': ctx.guild.id,
            'level': level,
            'pkmn': (boss.id, boss.quickMoveid or None, boss.chargeMoveid or None) if boss else (None, None, None),
            'hatch': hatch,
            'endtime': end,
            'messages': new_raid.message_ids,
            'channels': new_raid.channel_ids
        }
        insert.row(**data)
        insert.returning('id')
        rcrd = await insert.commit()
        new_raid.id = rcrd[0][0]
        ctx.bot.add_listener(new_raid.on_raw_reaction_add)
        await new_raid.monitor_status()
        
        
        
        
    
    @command()
    @checks.is_co_owner()
    async def countersupdate(self, ctx):
        data_table = ctx.bot.dbi.table('counters_data')
        raid_lists = ctx.bot.raid_info.raid_lists
        weather_list = ['CLEAR', 'PARTLY_CLOUDY', 'OVERCAST', 'RAINY', 'SNOW', 'FOG', 'WINDY', 'NO_WEATHER']
        ctrs_data_list = []
        for level in raid_lists:
            for pkmnid in raid_lists[level]:
                if level == 'EX':
                    url_level = 5
                else:
                    url_level = level
                for weather in weather_list:
                    data_url = Raid.pokebattler_data_url(
                        pkmnid, url_level, weather
                    )
                    async with aiohttp.ClientSession() as session:
                        async with session.get(data_url) as resp:
                            try:
                                data = await resp.json()
                                data = data['attackers'][0]
                            except KeyError:
                                print(data_url)
                    random_move_ctrs = data['randomMove']['defenders'][-6:]
                    estimator = data['randomMove']['total']['estimator']
                    random_move_dict = {
                        'boss_id': pkmnid,
                        'level': level,
                        'weather': weather,
                        'fast_move': 'random',
                        'charge_move': 'random',
                        'estimator': estimator
                    }
                    ctr_index = 1
                    for ctr in reversed(random_move_ctrs):
                        ctrid = ctr['pokemonId']
                        moveset = ctr['byMove'][-1]
                        fast_move = moveset['move1']
                        charge_move = moveset['move2']
                        random_move_dict[f'counter_{ctr_index}_id'] = ctrid
                        random_move_dict[f'counter_{ctr_index}_fast'] = fast_move
                        random_move_dict[f'counter_{ctr_index}_charge'] = charge_move
                        ctr_index += 1
                    ctrs_data_list.append(random_move_dict)
                    for moveset in data['byMove']:
                        ctrs = moveset['defenders'][-6:]
                        boss_fast = moveset['move1']
                        boss_charge = moveset['move2']
                        estimator = moveset['total']['estimator']
                        moveset_dict = {
                            'boss_id': pkmnid,
                            'level': level,
                            'weather': weather,
                            'fast_move': boss_fast,
                            'charge_move': boss_charge,
                            'estimator': estimator
                        }
                        ctr_index = 1
                        for ctr in reversed(ctrs):
                            ctrid = ctr['pokemonId']
                            moveset = ctr['byMove'][-1]
                            fast_move = moveset['move1']
                            charge_move = moveset['move2']
                            moveset_dict[f'counter_{ctr_index}_id'] = ctrid
                            moveset_dict[f'counter_{ctr_index}_fast'] = fast_move
                            moveset_dict[f'counter_{ctr_index}_charge'] = charge_move
                            ctr_index += 1
                        ctrs_data_list.append(moveset_dict)
        
        insert = data_table.insert().rows(ctrs_data_list)
        return await insert.commit(do_update=True)



