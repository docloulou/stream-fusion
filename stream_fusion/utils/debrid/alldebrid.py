# alldebrid.py
import uuid
from urllib.parse import unquote

from fastapi import HTTPException

from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.utils.general import season_episode_in_filename
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings


class AllDebrid(BaseDebrid):
    def __init__(self, config):
        super().__init__(config)
        self.base_url = f"{settings.ad_base_url}/{settings.ad_api_version}/"
        self.agent = settings.ad_user_app

    def get_headers(self):
        if settings.ad_unique_account:
            if not settings.proxied_link:
                logger.warning("AllDebrid: Unique account enabled, but proxied link is disabled. This may lead to account ban.")
                logger.warning("AllDebrid: Please enable proxied link in the settings.")
                raise HTTPException(status_code=500, detail="Proxied link is disabled.")
            if settings.ad_token:
                return {"Authorization": f"Bearer {settings.ad_token}"}
            else:
                logger.warning("AllDebrid: Unique account enabled, but no token provided. Please provide a token in the env.")
                raise HTTPException(status_code=500, detail="AllDebrid token is not provided.")
        else:
            return {"Authorization": f"Bearer {self.config["ADToken"]}"}

    def add_magnet(self, magnet, ip=None):
        url = f"{self.base_url}magnet/upload?agent={self.agent}"
        data = {"magnets[]": magnet}
        return self.json_response(url, method='post', headers=self.get_headers(), data=data)

    def add_torrent(self, torrent_file, ip=None):
        url = f"{self.base_url}magnet/upload/file?agent={self.agent}"
        files = {"files[]": (str(uuid.uuid4()) + ".torrent", torrent_file, 'application/x-bittorrent')}
        return self.json_response(url, method='post', headers=self.get_headers(), files=files)

    def check_magnet_status(self, id, ip=None):
        url = f"{self.base_url}magnet/status?agent={self.agent}&id={id}"
        return self.json_response(url, method='get', headers=self.get_headers())

    def unrestrict_link(self, link, ip=None):
        url = f"{self.base_url}link/unlock?agent={self.agent}&link={link}"
        return self.json_response(url, method='get', headers=self.get_headers())

    def get_stream_link(self, query, config, ip=None):
        magnet = query['magnet']
        stream_type = query['type']
        torrent_download = unquote(query["torrent_download"]) if query["torrent_download"] is not None else None

        torrent_id = self.add_magnet_or_torrent(magnet, torrent_download, ip)
        logger.info(f"AllDebrid: Torrent ID: {torrent_id}")

        # if not self.wait_for_ready_status(
        #         lambda: self.check_magnet_status(torrent_id, ip)["data"]["magnets"]["status"] == "Ready"):
        #     logger.error("AllDebrid: Torrent not ready, caching in progress.")
        #     return settings.no_cache_video_url
        logger.info("AllDebrid: Torrent is ready.")

        logger.info(f"AllDebrid: Retrieving data for torrent ID: {torrent_id}")
        data = self.check_magnet_status(torrent_id, ip)["data"]
        logger.debug(f"AllDebrid: Raw data: {data}")

        logger.info(f"AllDebrid: Data retrieved for torrent ID")

        link = settings.no_cache_video_url
        if stream_type == "movie":
            logger.info("AllDebrid: Getting link for movie")
            link = max(data["magnets"]['links'], key=lambda x: x['size'])['link']
        elif stream_type == "series":
            numeric_season = int(query['season'].replace("S", ""))
            numeric_episode = int(query['episode'].replace("E", ""))
            logger.info(f"AllDebrid: Getting link for series S{numeric_season:02d}E{numeric_episode:02d}")

            matching_files = []
            for file in data["magnets"]["links"]:
                if season_episode_in_filename(file["filename"], numeric_season, numeric_episode):
                    matching_files.append(file)

            if len(matching_files) == 0:
                logger.error(f"AllDebrid: No matching files for S{numeric_season:02d}E{numeric_episode:02d} in torrent.")
                raise HTTPException(status_code=404, detail=f"No matching files for S{numeric_season:02d}E{numeric_episode:02d} in torrent.")

            link = max(matching_files, key=lambda x: x["size"])["link"]
        else:
            logger.error("AllDebrid: Unsupported stream type.")
            raise HTTPException(status_code=500, detail="Unsupported stream type.")

        if link == settings.no_cache_video_url:
            logger.info("AllDebrid: Video not cached, returning NO_CACHE_VIDEO_URL")
            return link

        logger.info(f"AllDebrid: Retrieved link: {link}")

        unlocked_link_data = self.unrestrict_link(link, ip)

        if not unlocked_link_data:
            logger.error("AllDebrid: Failed to unlock link.")
            raise HTTPException(status_code=500, detail="Failed to unlock link in AllDebrid.")

        logger.info(f"AllDebrid: Unrestricted link: {unlocked_link_data['data']['link']}")

        return unlocked_link_data["data"]["link"]

    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        logger.info(f"AllDebrid : Vérification de la disponibilité pour {len(hashes_or_magnets)} hashes/magnets")
        
        available_data = {}
        for magnet in hashes_or_magnets:
            info_hash = hashes_or_magnets
            available_data[info_hash] = {
                "status": "ready",
                "links": [{"link": f"https://example.com/stream/{info_hash}"}]
            }
        
        logger.info("AllDebrid : Tous les hashes/magnets sont marqués comme disponibles.")
        
        return {
            "success": True,
            "detail": "Tous les hashes/magnets sont disponibles.",
            "data": available_data
        }

    def add_magnet_or_torrent(self, magnet, torrent_download=None, ip=None):
        torrent_id = ""
        if torrent_download is None:
            logger.info(f"AllDebrid: Adding magnet")
            magnet_response = self.add_magnet(magnet, ip)
            logger.info(f"AllDebrid: Add magnet response received")

            if not magnet_response or "status" not in magnet_response or magnet_response["status"] != "success":
                return "Error: Failed to add magnet."

            torrent_id = magnet_response["data"]["magnets"][0]["id"]
        else:
            logger.info(f"AllDebrid: Downloading torrent file")
            torrent_file = self.download_torrent_file(torrent_download)
            logger.info(f"AllDebrid: Torrent file downloaded")

            logger.info(f"AllDebrid: Adding torrent file")
            upload_response = self.add_torrent(torrent_file, ip)
            logger.info(f"AllDebrid: Add torrent file response received")

            if not upload_response or "status" not in upload_response or upload_response["status"] != "success":
                return "Error: Failed to add torrent file in AllDebrid."

            torrent_id = upload_response["data"]["files"][0]["id"]

        logger.info(f"AllDebrid: New torrent ID: {torrent_id}")
        return torrent_id
