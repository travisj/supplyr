import os
import uuid
import datetime

import tornado.httpserver
import tornado.ioloop
import tornado.web
import pymongo
from pymongo.objectid import ObjectId 

connection = pymongo.Connection()
db = connection.supplyr
ads = db.ads
cookies = db.cookies
impressions = db.impressions

class BaseHandler(tornado.web.RequestHandler):
	pass

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
		for ad in ads.find({"size":size, "state":"active"}).sort("price", pymongo.DESCENDING):
			if not marker_found:
				print 'checking: %s == %s' % (marker, str(ad['_id']))
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

		print 'ended up here'


class MainHandler(BaseHandler):
	def get(self):
		all_ads = ads.find()
		self.render("index.html", ads=all_ads)

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
	def get(self, id):
		ad = {}
		if id is not None:
			ad = ads.find_one({'_id':ObjectId(id)})
		self.render("admin/ad.html", ad=ad)

	def post(self, id):
		#id = self.get_argument("id", None)
		print id
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


settings = {
	"static_path": os.path.join(os.path.dirname(__file__), "static"),
	"template_path": os.path.join(os.path.dirname(__file__), "templates"),
	"debug": True,
}

application = tornado.web.Application([
	(r"/", MainHandler),
	(r"/cookie", CookieHandler),
	(r"/iframe", IframeHandler),
	(r"/reset", ResetHandler),
	(r"/admin/ad/([^/]+)?", AdminAdHandler),
], **settings)

if __name__ == "__main__":
	http_server = tornado.httpserver.HTTPServer(application)
	http_server.listen(8888)
	tornado.ioloop.IOLoop.instance().start()
