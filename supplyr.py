import os
import uuid
import datetime
import hashlib
import locale

import functools
import tornado.httpserver
import tornado.ioloop
import tornado.web
from tornado.options import options, define

import pymongo
from pymongo.objectid import ObjectId 

define("debug", default='off', help="on or off to turn on debugging", type=str)
define("port", default=8888, type=int)


connection = pymongo.Connection()
db = connection.supplyr
ads = db.ads
cookies = db.cookies
impressions = db.impressions

locale.setlocale( locale.LC_ALL, '' )

def administrator(method):
	"""Decorate with this method to restrict to site admins."""
	@functools.wraps(method)
	def wrapper(self, *args, **kwargs):
		if not self.current_user:
			if self.request.method == "GET":
				self.redirect(self.get_login_url())
				return
			raise web.HTTPError(403)
		else:
			return method(self, *args, **kwargs)
	return wrapper


class BaseHandler(tornado.web.RequestHandler):
	"""Implements Google Accounts authentication methods."""
	def get_current_user(self):
		session = self.get_session()
		user = False
		if session:
			user = db.users.find_one({'_id':session['user']['_id']})

		if user:
			return user
		else:
			return False

	def start_session(self):
		self.session_id = self.get_cookie('session_id')
		if not self.session_id:
			self.session_id = str(uuid.uuid4())
			self.set_cookie('session_id', self.session_id)
	
	def get_session(self):
		self.start_session()
		session = db.sessions.find_one({'id':self.session_id})
		if not session:
			session = {}
		return session

	def delete_session(self):
		self.start_session()
		db.sessions.remove({'id':self.session_id})

	def set_in_session(self, key, value):
		session = self.get_session()
		session['id'] = self.session_id
		session[key] = value
		db.sessions.save(session)

	def get_login_url(self):
		# return users.create_login_url(self.request.uri)
		return '/login'

	def render_string(self, template_name, **kwargs):
		return tornado.web.RequestHandler.render_string(self, template_name,  **kwargs)

class AdServerHandler(BaseHandler):
	def reset_ad_cookie(self):
		self.set_cookie('uuid', '')

	def get_ad_cookie(self):
		self.uuid = self.get_cookie('uuid')
		if not self.uuid:
			self.uuid = str(uuid.uuid4())
			self.set_cookie('uuid', self.uuid)
		
		stored_cookie = cookies.find_one({'uuid':self.uuid})
		if stored_cookie:
			now = datetime.datetime.utcnow()
			if stored_cookie.get('today'):
				today = stored_cookie.get('today')
				timediff = now - today
				if timediff.seconds > 86400: # one day
					cookie = {
						'uuid': self.uuid,
						'today': datetime.datetime.utcnow(),
					}
					cookies.update({'uuid':self.uuid}, {'$set': {'today': datetime.datetime.utcnow(), 'creative': {}}})
				else:
					cookie = stored_cookie
			else:
				cookie = {
					'uuid': self.uuid,
					'today': datetime.datetime.utcnow(),
				}
		else:
			cookie = {
				'uuid': self.uuid,
				'today': datetime.datetime.utcnow(),
			}
			self.set_ad_cookie(cookie)
		return cookie
	
	def set_ad_cookie(self, cookie):
		cookie['ip'] = self.request.remote_ip
		cookies.save(cookie)

	def get_creative(self, creative_id):
		return ads.find_one({'_id':ObjectId(str(creative_id))})

	def get_ad_to_serve(self):
		size = self.get_argument('size')
		marker = self.get_argument('marker', None)

		marker_found = False
		if not marker:
			marker_found = True
			tag_on_page = 1 # tag_on_page is a bad name, but it signifies that it has not been passed back as a default from a network
		else:
			tag_on_page = 0

		cookie = self.get_ad_cookie()
		for ad in ads.find({"size":size, "state":"active", "deleted": {'$ne': True}}).sort("price", pymongo.DESCENDING):
			if not marker_found:
				if marker == str(ad['_id']):
					marker_found = True
				continue

			frequency_for_ad = ad.get('frequency')
			frequency_for_user = None
			if cookie.get('creative'):
				frequency_for_user = cookie.get('creative').get('%s' % (ad['_id']), None)

			if int(frequency_for_ad) == 0 or frequency_for_user == None or int(frequency_for_user) < int(frequency_for_ad):
				cookies.update({'uuid':self.uuid}, {'$inc': {'creative.%s' % (ad['_id']): 1}})
				db.raw_impressions.save({'ad_id':str(ad['_id']), 'date':datetime.datetime.utcnow(), 'uuid': self.uuid, 'tag_on_page': tag_on_page})
				return ad

class MainHandler(BaseHandler):
	@administrator
	def get(self):
		all_ads = ads.find({'deleted': {'$ne': True }}).sort("size", pymongo.DESCENDING).sort("price", pymongo.DESCENDING)
		self.render("index.html", ads=all_ads, format_currency=self.format_currency)

	def format_currency(self, number):
		return locale.currency(float(number))

class CookieHandler(AdServerHandler):
	def get(self):
		crs = []
		cookie = self.get_ad_cookie()
		creative_views = cookie.get('creative', None)
		if creative_views:
			for creative in creative_views:
				cr = self.get_creative(creative)
				if cr:
					crs.append(cr)

		self.render('cookie.html', creatives=crs, creative_views=creative_views)


class IframeHandler(AdServerHandler):
	def get(self):
		ad = self.get_ad_to_serve()
	
		self.write("<HTML><BODY>")
		self.write(ad['tag'])
		self.write("</BODY></HTML>")


class ResetHandler(AdServerHandler):
	def get(self):
		self.reset_ad_cookie()


class AdminAdHandler(BaseHandler):
	@administrator
	def get(self, id):
		ad = {}
		if id is not None:
			ad = ads.find_one({'_id':ObjectId(id)})
		self.render("admin/ad.html", ad=ad)

	@administrator
	def post(self, id):
		name = self.get_argument("name", "unnamed")
		state = self.get_argument("state", "inactive")
		size = self.get_argument('size', None)
		price = self.get_argument("price", 0)
		frequency = self.get_argument("frequency", 0)
		tag = self.get_argument("tag")

		ad = {
			"name":name,
			"size":size,
			"state":state,
			"price":price,
			"frequency":frequency,
			"tag":tag,
		}

		if id:
			#ad = ads.find_one({'_id':ObjectId(id)})
			ads.update({'_id':ObjectId(id)}, {"$set":ad})
		else:
			ads.insert(ad)

		self.redirect("/")

class AdminUsersHandler(BaseHandler):
	@administrator
	def get(self, login):
		users = db.users.find()
		user = None
		if login:
			user = db.users.find_one({'login': login})

		self.render('admin/users.html', users=users, user=user)

	@administrator
	def post(self, login):
		id = self.get_argument('id', None)
		login = self.get_argument('login')
		password = self.get_argument('password', None)

		user = {}
		user['login'] = login

		if id:
			print id
			if password:
				user['password'] = hashlib.sha1(password).hexdigest()
			db.users.update({'_id':ObjectId(str(id))}, {"$set":user})
		else:
			user['password'] = hashlib.sha1(password).hexdigest()
			db.users.insert(user)

		self.redirect('/admin/users/')

class DeleteHandler(BaseHandler):
	@administrator
	def get(self, id):
		db.ads.update({'_id':ObjectId(id)}, {'$set': {'deleted': True}});
		self.redirect('/')
		
class LoginHandler(BaseHandler):
	def get(self):
		self.render("login.html")

	def post(self):
		# check login info
		login = self.get_argument('login')
		password = self.get_argument('password')
		
		user = db.users.find_one({'login':login, 'password': hashlib.sha1(password).hexdigest()})
		if user:
			self.set_in_session('user', user)
			self.redirect('/')
		else:
			self.render("login.html")

class LogoutHandler(BaseHandler):
	def get(self):
		self.delete_session()
		self.redirect('/')


if __name__ == "__main__":
	tornado.options.parse_command_line()

	settings = {
		"static_path": os.path.join(os.path.dirname(__file__), "static"),
		"template_path": os.path.join(os.path.dirname(__file__), "templates"),
		"debug": True if options.debug == 'on' else False,
	}

	application = tornado.web.Application([
		(r"/", MainHandler),
		(r"/cookie", CookieHandler),
		(r"/iframe", IframeHandler),
		(r"/reset", ResetHandler),
		(r"/login", LoginHandler),
		(r"/logout", LogoutHandler),
		(r"/admin/users/([^/]+)?", AdminUsersHandler),
		(r"/admin/ad/([^/]+)?", AdminAdHandler),
		(r"/admin/delete/([^/]+)?", DeleteHandler),
	], **settings)

	http_server = tornado.httpserver.HTTPServer(application)
	http_server.listen(options.port)
	tornado.ioloop.IOLoop.instance().start()
