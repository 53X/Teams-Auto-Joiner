import json
import time
from termcolor import colored
import re

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common import exceptions

browser = None
config = None
active_meeting = None
uuid_regex = r"\b[0-9a-f]{8}\b-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-\b[0-9a-f]{12}\b"


class Meeting:
    def __init__(self, started_at, id):
        self.started_at = started_at
        self.id = id


class Channel:
    def __init__(self, name, meetings, blacklisted=False):
        self.name = name
        self.meetings = meetings
        self.blacklisted = blacklisted

    def __str__(self):
        return colored(self.name, 'red') if self.blacklisted else self.name

    def get_elem(self, parent):
        try:
            channel_elem = parent.find_element_by_css_selector(f"ul>ng-include>li[data-tid*='channel-{self.name}-li']")
        except exceptions.NoSuchElementException:
            return None

        return channel_elem


class Team:
    def __init__(self, name, elem, channels=None):
        if channels is None:
            channels = []
        self.name = name
        self.elem = elem
        self.channels = channels

    def __str__(self):
        channel_string = '\n\t'.join([str(channel) for channel in self.channels])

        return f"{self.name}\n\t{channel_string}"

    def expand_channels(self):
        try:
            elem = self.elem.find_element_by_css_selector("div[class='channels']")
        except exceptions.NoSuchElementException:
            try:
                self.elem.click()
                elem = self.elem.find_element_by_css_selector("div[class='channels']")
            except exceptions.NoSuchElementException:
                return None
        return elem

    def init_channels(self):
        channels_elem = self.expand_channels()

        channel_elems = channels_elem.find_elements_by_css_selector("ul>ng-include>li")

        channel_names = [channel_elem.get_attribute("data-tid") for channel_elem in channel_elems]
        channel_names = [channel_name[channel_name.find('-channel-') + 9:channel_name.rfind("-li")] for channel_name
                         in
                         channel_names]

        self.channels = [Channel(channel_names[i], []) for i in range(len(channel_elems))]

    def check_blacklist(self):
        blacklist = config['blacklist']
        blacklist_item = next((team for team in blacklist if team['team_name'] == self.name),  None)
        if blacklist_item is None:
            return

        if len(blacklist_item['channel_names']) == 0:
            for channel in self.channels:
                channel.blacklisted = True
        else:
            blacklist_channels = [x for x in self.channels if x.name in blacklist_item['channel_names']]
            for blacklist_channel in blacklist_channels:
                blacklist_channel.blacklisted = True

    def update_meetings(self):
        channels = self.expand_channels()

        for channel in self.channels:
            if channel.blacklisted:
                continue

            channel_elem = channel.get_elem(channels)
            try:
                active_meeting_elem = channel_elem.find_element_by_css_selector(
                    "a>active-calls-counter[is-meeting='true']")
            except exceptions.NoSuchElementException:
                continue

            active_meeting_elem.click()

            if wait_till_found("button[ng-click='ctrl.joinCall()']", 60) is None:
                continue

            join_meeting_elems = browser.find_elements_by_css_selector("button[ng-click='ctrl.joinCall()']")
            meeting_ids = [re.search(uuid_regex, join_meeting_elem.get_attribute('track-data')).group(0) for
                           join_meeting_elem in join_meeting_elems]

            # remove duplicates
            meeting_ids = list(dict.fromkeys(meeting_ids))

            for meeting_id in meeting_ids:
                if meeting_id not in [meeting.id for meeting in channel.meetings]:
                    channel.meetings.append(Meeting(time.time(), meeting_id))

    def update_elem(self):
        self.elem = browser.find_element_by_css_selector(
            f"ul>li[role='treeitem'][class='match-parent team left-rail-item-kb-l2']>div[data-tid='team-{self.name}-li']")


def wait_till_found(sel, timeout):
    try:
        element_present = EC.presence_of_element_located((By.CSS_SELECTOR, sel))
        WebDriverWait(browser, timeout).until(element_present)

        return browser.find_element_by_css_selector(sel)
    except exceptions.TimeoutException:
        print("Timeout waiting for element.")
        return None


def get_teams():
    # find all team names
    team_elems = browser.find_elements_by_css_selector(
        "ul>li[role='treeitem'][class='match-parent team left-rail-item-kb-l2']>div")
    team_names = [team_elem.get_attribute("data-tid") for team_elem in team_elems]
    team_names = [team_name[team_name.find('team-') + 5:team_name.rfind("-li")] for team_name in team_names]

    team_list = [Team(team_names[i], team_elems[i], None) for i in range(len(team_elems))]
    return team_list


def join_newest_meeting(teams):
    global active_meeting

    meeting_to_join = Meeting(-1, None) if active_meeting is None else active_meeting
    meeting_team = None
    meeting_channel = None

    for team in teams:
        for channel in team.channels:
            if channel.blacklisted:
                continue

            for meeting in channel.meetings:
                if meeting.started_at > meeting_to_join.started_at:
                    meeting_to_join = meeting
                    meeting_team = team
                    meeting_channel = channel

    if meeting_team is None:
        return False

    hangup()

    channels_elem = meeting_team.expand_channels()

    meeting_channel.get_elem(channels_elem).click()

    join_btn = wait_till_found(f"button[track-data*='{meeting_to_join.id}']", 30)
    if join_btn is None:
        return

    join_btn.click()

    join_now_btn = wait_till_found("button[data-tid='prejoin-join-button']", 30)
    if join_now_btn is None:
        return
    join_now_btn.click()

    browser.find_element_by_css_selector("span[data-tid='appBarText-Teams']").click()

    active_meeting = meeting_to_join
    return True


def hangup():
    try:
        hangup_btn = browser.find_element_by_css_selector("button[data-tid='call-hangup']")
        hangup_btn.click()
    except exceptions.NoSuchElementException:
        return


def main():
    global browser, config

    chrome_options = webdriver.ChromeOptions()
    browser = webdriver.Chrome(chrome_options=chrome_options)

    with open('../config.json') as json_data_file:
        config = json.load(json_data_file)

    browser.get("https://teams.microsoft.com")

    if config['email'] != "" and config['password'] != "":
        login_email = wait_till_found("input[type='email']", 60)
        if login_email is None:
            exit(1)

        login_email.send_keys(config['email'])
        time.sleep(1)
        browser.find_element_by_css_selector("input[type='submit']").click()

        login_pwd = wait_till_found("input[type='password']", 60)
        if login_pwd is None:
            exit(1)

        login_pwd.send_keys(config['password'])
        time.sleep(1)
        browser.find_element_by_css_selector("input[type='submit']").click()

    print("Waiting for correct page...")
    if wait_till_found("div[data-tid='team-channel-list']", 60 * 5) is None:
        exit(1)

    teams = get_teams()
    for team in teams:
        team.init_channels()
        team.check_blacklist()

    for team in teams:
        print(team)

    sel_str = "\nStart [s], Reload teams [r], quit[q]\n"

    selection = input(sel_str).lower()
    while selection != 's':
        if selection == 'q':
            exit(0)
        if selection == 'r':
            teams = get_teams()
            for team in teams:
                team.init_channels()
                team.check_blacklist()

            for team in teams:
                print(team)

        selection = input(sel_str).lower()

    while 1:
        time.sleep(2)
        for team in teams:
            team.update_meetings()

        if join_newest_meeting(teams):
            for team in teams:
                team.update_elem()


if __name__ == "__main__":
    main()