import discord
from discord.ext import commands, tasks # Adicionado 'tasks' para a função de loop
import asyncio
import os # Importar o módulo 'os' para lidar com operações do sistema operacional
import yt_dlp # Importar a biblioteca yt-dlp
from collections import deque # Importar deque para a fila de músicas
import random # Importar o módulo 'random' para escolher um áudio aleatório

# --- Fila de músicas global ---
music_queue = deque() # Usando deque para adições e remoções eficientes

# --- Configuração do Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Função auxiliar para tocar música (local ou YouTube) ---
# Esta função será chamada pelo comando 'play' e pela função de callback 'after_playing_callback'
async def play_music(ctx, query):
    voice_client = ctx.guild.voice_client

    if not (voice_client and voice_client.is_connected()):
        await ctx.send('O bot não está conectado a um canal de voz.')
        return

    try:
        source = None
        display_title = query # Título padrão para exibição

        if query.startswith('http://') or query.startswith('https://'):
            # É uma URL do YouTube
            await ctx.send(f'Buscando música no YouTube: `{query}`...')
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'noplaylist': True, # Não processar playlists, apenas o primeiro vídeo
                'quiet': True, # Suprime mensagens de erro do yt-dlp para o console
                'no_warnings': True, # Suprime avisos
                'default_search': 'ytsearch', # Se a query não for URL, tenta buscar no YouTube
                'extractor_args': {
                    'youtube': {
                        'skip': ['dash'] # Pula manifestos DASH, que podem causar problemas
                    }
                }
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False) # Não baixa, apenas extrai informações
                # Se a query foi um termo de busca, info pode ser um objeto de playlist/entradas
                if 'entries' in info:
                    info = info['entries'][0] # Pega o primeiro resultado da busca
                url = info['url'] # Pega a URL do stream direto do áudio
                display_title = info.get('title', 'Música do YouTube') # Pega o título para exibição

            # Configurações para FFmpeg para ajudar na reconexão em caso de interrupção do stream
            source = discord.FFmpegPCMAudio(url, before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
            await ctx.send(f'Tocando do YouTube: **{display_title}**')
        else:
            # É um nome de arquivo local
            file_to_play = query
            if not file_to_play.lower().endswith('.mp3'):
                file_to_play += '.mp3'
            display_title = file_to_play.replace('.mp3', '') # Remove .mp3 para exibição
            file_path = f'./audios/{file_to_play}'  # Adiciona o caminho da pasta
            source = discord.FFmpegPCMAudio(file_path)
            await ctx.send(f'Tocando local: **{display_title}**')

        # Esta função de callback (`after`) roda em um Thread Pool Executor (outro thread).
        # Para interagir com o loop de eventos do Discord (que é assíncrono), precisamos agendar.
        def after_playing_callback(error):
            if error:
                print(f'Erro de reprodução (after callback): {error}')
            print(f"Reprodução de '{display_title}' concluída ou interrompida.")

            # Agendamos a próxima música para ser tocada no loop principal do bot.
            # Usamos ctx.guild.id e ctx.channel.id para encontrar o contexto novamente.
            bot.loop.call_soon_threadsafe(
                asyncio.create_task, # Cria uma tarefa para a corrotina
                play_next_song_from_queue(ctx.guild.id, ctx.channel.id)
            )

        voice_client.play(source, after=after_playing_callback)

    except FileNotFoundError:
        await ctx.send(f'Arquivo local não encontrado: `{query}`. Verifique o nome e se está na pasta correta.')
    except yt_dlp.utils.DownloadError as e:
        await ctx.send(f'Erro ao buscar do YouTube: `{e}`. Certifique-se de que a URL/pesquisa é válida.')
        print(f'Erro yt-dlp: {e}')
    except Exception as e:
        await ctx.send(f'Ocorreu um erro inesperado durante a reprodução: {e}')
        print(f'Erro geral de reprodução: {e}')

# Corrotina auxiliar para tocar a próxima música da fila
async def play_next_song_from_queue(guild_id, channel_id):
    guild = bot.get_guild(guild_id)
    if not guild:
        print(f"Guild {guild_id} não encontrada para reprodução da fila.")
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        print(f"Canal {channel_id} não encontrado para reprodução da fila na guild {guild_id}.")
        return

    # Criamos um objeto de contexto (ctx) minimalista para usar a função play_music
    class DummyContext:
        def __init__(self, guild_obj, channel_obj, bot_instance):
            self.guild = guild_obj
            self.channel = channel_obj
            self.bot = bot_instance
        async def send(self, content):
            await self.channel.send(content)
        @property
        def author(self): # Minimal author para consistência, não estritamente necessário para play_music
            return self.bot.user

    mock_ctx = DummyContext(guild, channel, bot)

    if len(music_queue) > 0:
        next_query = music_queue.popleft() # Pega a próxima música da fila
        await play_music(mock_ctx, next_query) # Chama a função auxiliar para tocar
    else:
        print("Fila de reprodução vazia, parando.")
        # Opcionalmente, pode desconectar o bot aqui se não houver mais músicas
        # voice_client = guild.voice_client
        # if voice_client and voice_client.is_connected():
        #     await voice_client.disconnect()


# --- Eventos do Bot ---
@bot.event
async def on_ready():
    print(f'Bot logado como {bot.user.name} (ID: {bot.user.id})')
    print('------')
    # Inicia a nova tarefa em segundo plano quando o bot estiver pronto
    play_random_sound_loop.start()

# --- NOVA FUNÇÃO: Loop para tocar áudio aleatório ---
# Usamos o decorador `tasks.loop` para criar uma tarefa que roda em intervalos definidos.
# `seconds=60` significa que o código dentro desta função será executado a cada 60 segundos.
@tasks.loop(seconds=30)
async def play_random_sound_loop():
    # Define a chance de tocar um som. 0.15 representa 15%.
    chance_to_play = 0.15
    
    # `random.random()` gera um número entre 0.0 e 1.0.
    # Se o número gerado for menor que a nossa chance, a condição é satisfeita.
    if random.random() < chance_to_play:
        print("Sorteado para tocar um som aleatório!")

        # Itera sobre todos os servidores (guilds) em que o bot está.
        for guild in bot.guilds:
            # Pega o cliente de voz do bot para este servidor específico.
            voice_client = guild.voice_client
            
            # Condições para tocar o som aleatório:
            # 1. O bot precisa estar conectado a um canal de voz (`voice_client` existe e `is_connected()`).
            # 2. O bot NÃO pode estar tocando algo no momento (`not voice_client.is_playing()`).
            # 3. A fila de músicas deve estar vazia (`len(music_queue) == 0`).
            # Isso garante que a função não vai interromper músicas pedidas pelos usuários.
            if voice_client and voice_client.is_connected() and not voice_client.is_playing() and len(music_queue) == 0:
                
                # Procura por arquivos .mp3 na pasta do bot (mesma lógica do comando !lista).
                local_audio_files = [f for f in os.listdir('./audios') if f.lower().endswith('.mp3')]
                
                # Se encontrar algum arquivo .mp3...
                if local_audio_files:
                    # Escolhe um nome de arquivo aleatoriamente da lista.
                    random_song = random.choice(local_audio_files)
                    
                    # Remove a extensão .mp3 para não precisar digitar no comando `play_music`.
                    song_name_without_extension = random_song.replace('.mp3', '')

                    print(f"Tocando som aleatório '{song_name_without_extension}' no servidor '{guild.name}'")

                    # Para usar a função `play_music`, precisamos de um objeto 'contexto' (ctx).
                    # Como estamos em um loop sem um comando de usuário, criamos um 'falso'.
                    # A parte mais importante é ter `guild` e `channel` para que `play_music` saiba onde tocar.
                    class DummyContext:
                        def __init__(self, guild_obj, channel_obj):
                            self.guild = guild_obj
                            self.channel = channel_obj
                        
                        # `play_music` usa `ctx.send`, então criamos uma função vazia para evitar erros.
                        async def send(self, content):
                            # Não precisamos que o bot envie mensagens para o som aleatório, então só damos um 'pass'.
                            # Se quiser que ele anuncie, pode colocar `await self.channel.send(content)` aqui.
                            pass

                    # O `voice_client.channel` nos dá o canal de voz onde o bot está conectado.
                    mock_ctx = DummyContext(guild, voice_client.channel)
                    
                    # Finalmente, chamamos a função de tocar música com o som aleatório escolhido.
                    await play_music(mock_ctx, song_name_without_extension)

# --- Comandos do Bot ---
@bot.command()
async def hello(ctx):
    """Responde com uma mensagem de saudação."""
    await ctx.send(f'Olá, {ctx.author.name}!')

@bot.command()
async def ping(ctx):
    """Mostra a latência do bot."""
    await ctx.send(f'Pong! {round(bot.latency * 1000)}ms')

# --- Comandos de Áudio ---
@bot.command()
async def connect(ctx):
    """Conecta o bot ao canal de voz do usuário que invocou o comando."""
    if ctx.author.voice: # Verifica se o usuário está em um canal de voz
        channel = ctx.author.voice.channel
        try:
            await channel.connect()
            await ctx.send(f'Conectado ao canal: **{channel.name}**')
        except discord.ClientException:
            # Se o bot já estiver conectado
            await ctx.send('Já estou conectado a um canal de voz neste servidor.')
        except Exception as e: # Captura qualquer outra exceção genérica
            await ctx.send(f'Ocorreu um erro ao tentar conectar: {e}')
            print(f'Erro de conexão de voz inesperado: {e}')
    else:
        # Se o usuário não estiver em um canal de voz
        await ctx.send('Você precisa estar conectado a um canal de voz para usar este comando.')

@bot.command()
async def play(ctx, *, query: str): # Usamos *, query para permitir espaços na string de pesquisa
    """Toca um arquivo de áudio local, uma música/pesquisa do YouTube ou uma playlist inteira.
    Ex: !play nome_do_audio
    Ex: !play https://www.youtube.com/watch?v=dQw4w9WgXcQ
    Ex: !play Never Gonna Give You Up Rick Astley
    Ex: !play https://www.youtube.com/playlist?list=PLczM1b0_22aB5n3AnS4l2aBbfD3aG4h45
    """
    voice_client = ctx.guild.voice_client

    # Tenta conectar o bot se ele não estiver conectado e o usuário estiver em um canal de voz
    if not (voice_client and voice_client.is_connected()):
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            try:
                voice_client = await channel.connect()
                await ctx.send(f'Conectado ao canal: **{channel.name}**')
            except discord.ClientException:
                await ctx.send('Já estou conectado a um canal de voz neste servidor.')
                # Pega o voice_client existente se a exceção for lançada
                voice_client = ctx.guild.voice_client
            except Exception as e:
                await ctx.send(f'Não foi possível conectar ao canal de voz: {e}')
                print(f'Erro de conexão implícita: {e}')
                return
        else:
            await ctx.send('Você precisa estar conectado a um canal de voz para que o bot possa conectar.')
            return

    # --- NOVA LÓGICA PARA LIDAR COM PLAYLISTS ---
    # Verifica se a query é uma URL de playlist do YouTube
    # https://www.youtube.com/watch?v=vmuVH5GG-uw&list=PL6YkHd6sT5W5umkK2nWnpYpR-EFFfg0gF
    is_playlist = '&list=' in query 

    if is_playlist:
        await ctx.send('Detectei uma playlist! Processando e adicionando músicas à fila...')
        try:
            # Opções para extrair informações da playlist de forma rápida
            ydl_opts_playlist = {
                'extract_flat': 'in_playlist', # Extrai apenas informações básicas dos vídeos, muito mais rápido
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl:
                info = ydl.extract_info(query, download=False)
                
                if 'entries' in info and info['entries']:
                    was_playing = voice_client.is_playing()
                    songs_added = 0
                    
                    # Adiciona a URL de cada vídeo da playlist à fila
                    for entry in info['entries']:
                        if entry: # Garante que a entrada não é nula
                            video_url = entry.get('url') # A URL da página do vídeo
                            music_queue.append(video_url)
                            songs_added += 1
                    
                    if songs_added > 0:
                        playlist_title = info.get('title', 'Nome desconhecido')
                        await ctx.send(f'**{songs_added}** músicas da playlist "{playlist_title}" foram adicionadas à fila!')
                    else:
                        await ctx.send('Não consegui extrair nenhuma música da playlist.')
                        return

                    # Se nada estava tocando antes, inicia a reprodução da fila
                    if not was_playing:
                        await play_next_song_from_queue(ctx.guild.id, ctx.channel.id)
                else:
                    await ctx.send('Não consegui encontrar vídeos nesta playlist ou ela está vazia.')

        except Exception as e:
            await ctx.send(f'Ocorreu um erro ao processar a playlist: {e}')
            print(f"Erro ao processar playlist: {e}")
        
        return # Finaliza a execução do comando aqui, pois a playlist já foi tratada

    # --- LÓGICA ANTIGA PARA MÚSICA ÚNICA ---
    # Se uma música já está tocando ou a fila já tem itens, apenas adiciona a nova música
    if voice_client.is_playing() or len(music_queue) > 0:
        music_queue.append(query)
        await ctx.send(f'Adicionado à fila: **{query}**. Posição na fila: {len(music_queue)}')
    else:
        # Se nada estiver tocando e a fila estiver vazia, inicia a reprodução imediatamente
        await play_music(ctx, query)


@bot.command()
async def stop(ctx):
    """Para a reprodução de áudio."""
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        music_queue.clear() # Limpa a fila ao parar
        await ctx.send('Reprodução de áudio parada e fila limpa.')
    else:
        await ctx.send('Nenhum áudio está sendo tocado.')

@bot.command()
async def disconnect(ctx):
    """Desconecta o bot do canal de voz."""
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        music_queue.clear() # Limpa a fila ao desconectar
        await ctx.send('Desconectado do canal de voz e fila limpa.')
    else:
        await ctx.send('O bot não está conectado a um canal de voz.')

@bot.command()
async def lista(ctx):
    """Lista todos os arquivos de áudio .mp3 disponíveis na pasta do bot."""
    audio_files = []
    # Lista todos os arquivos no diretório atual (onde o bot está rodando)
    for filename in os.listdir('./audios'): # '.' representa o diretório atual
        # Verifica se o arquivo termina com '.mp3' (ignorando maiúsculas/minúsculas)
        if filename.lower().endswith('.mp3'):
            audio_files.append(filename)

    if audio_files:
        # Formata a lista para ser enviada no Discord
        # Cada nome de arquivo será em uma nova linha com um "- " na frente
        list_str = "\n".join([f"- {file.replace('.mp3', '')}" for file in audio_files])
        await ctx.send(f"**Áudios locais disponíveis:**\n```\n{list_str}\n```")
    else:
        await ctx.send("Nenhum arquivo .mp3 local encontrado na pasta do bot.")

@bot.command()
async def fila(ctx):
    """Mostra a fila de músicas."""
    if len(music_queue) == 0:
        await ctx.send("A fila de reprodução está vazia.")
    else:
        # Cria uma lista temporária para exibição para evitar modificar a fila real durante a iteração
        queue_display = []
        for i, song in enumerate(music_queue):
            display_name = song
            # Para URLs, vamos apenas mostrar a URL para manter o comando rápido
            if song.startswith('http://') or song.startswith('https://'):
                display_name = f"<{song}>" # Envolve em <> para evitar embeds no Discord
            elif song.lower().endswith('.mp3'):
                display_name = song.replace('.mp3', '')

            queue_display.append(f"{i+1}. {display_name}")

        await ctx.send(f"**Fila de reprodução:**\n```\n{'\n'.join(queue_display)}\n```")


@bot.command()
async def skip(ctx):
    """Pula a música atual e toca a próxima na fila."""
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        await ctx.send("Música pulada.")
        voice_client.stop() # Isso irá acionar o after_playing_callback para tocar a próxima música
    elif len(music_queue) > 0:
        await ctx.send("Nenhuma música tocando, mas iniciando a próxima da fila.")
        # Chama play_next_song_from_queue diretamente, pois nada está tocando
        await play_next_song_from_queue(ctx.guild.id, ctx.channel.id)
    else:
        await ctx.send("Nenhuma música tocando e a fila está vazia.")

@bot.command()
async def limparfila(ctx):
    """Limpa toda a fila de reprodução."""
    music_queue.clear()
    await ctx.send("Fila de reprodução limpa.")

# --- Execução do Bot ---
with open("key.txt", "r") as arquivo:
    minha_key = arquivo.read().strip()

bot.run(f"{minha_key}")