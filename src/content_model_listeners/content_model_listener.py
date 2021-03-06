'''
Created on 2010-07-20

@author: al
'''
import fcrepo.connection, time, ConfigParser, sys, feedparser, logging, os
from stomp.connect import Connection
from stomp.listener import ConnectionListener, StatsListener
from fcrepo.client import FedoraClient
from fcrepo.utils import NS
from stomp.exception import NotConnectedException
from optparse import OptionParser
from categories import FedoraMicroService
from yapsy.PluginManager import PluginManager

# Add the URI reference for Fedora content models to the available namespaces.
NS['fedoramodel'] = u"info:fedora/fedora-system:def/model#"

CONFIG_FILE_NAME = 'content_model_listener.cfg'

TOPIC_PREFIX = '/topic/fedora.contentmodel.'

class ContentModelListener(ConnectionListener):
    '''
    classdocs
    '''
    def __init__(self, content_models, host='localhost', port=61613, user='', passcode='', fedora_url=''):
        '''
        Constructor
        '''
        self.conn = Connection([(host, port)], user, passcode)
        self.conn.set_listener('', self)
        self.conn.start()
        logging.info('Connecting to STOMP server %(host)s on port %(port)s.' % {'host': host, 'port': port})
        self.transaction_id = None
        logging.info("Connecting to Fedora server at %(url)s" % {'url': fedora_url})
        self.fc = fcrepo.connection.Connection(fedora_url, username = user, password = passcode)
        self.client = FedoraClient(self.fc)
        
        
        # Create plugin manager
        self.manager = PluginManager(categories_filter = {"FedoraMicroService": FedoraMicroService})
        self.manager.setPluginPlaces(["plugins"])
        
        # Load plugins.
        self.manager.locatePlugins()
        self.manager.loadPlugins()
        self.contentModels = {}
        
        for plugin in self.manager.getPluginsOfCategory("FedoraMicroService"):
            # plugin.plugin_object is an instance of the plubin
            logging.info("Loading plugin: %(name)s for content model %(cmodel)s." % {'name': plugin.plugin_object.name, 'cmodel': plugin.plugin_object.content_model})
            plugin.plugin_object.config = config
            if plugin.plugin_object.content_model in self.contentModels:
                self.contentModels[plugin.plugin_object.content_model].append(plugin.plugin_object)
            else:
                self.contentModels[plugin.plugin_object.content_model] = [plugin.plugin_object]
    
    def __print_async(self, frame_type, headers, body):
        """
        Utility function for printing messages.
        """
        #logging.debug("\r  \r", end='')
        logging.debug(frame_type)
        for header_key in headers.keys():
            logging.debug('%s: %s' % (header_key, headers[header_key]))
        logging.debug(body)
    
    def on_connecting(self, host_and_port):
        """
        \see ConnectionListener::on_connecting
        """
        self.conn.connect(wait=True)
        
    def on_disconnected(self):
        """
        \see ConnectionListener::on_disconnected
        """
        
    def on_message(self, headers, body):
        """
        \see ConnectionListener::on_message
        """ 
        global TOPIC_PREFIX
        self.__print_async('MESSAGE', headers, body)
        f = feedparser.parse(body)
        tags = f['entries'][0]['tags']
        pid = [tag['term'] for tag in tags if tag['scheme'] == 'fedora-types:pid'][0]
        dsID = [tag['term'] for tag in tags if tag['scheme'] == 'fedora-types:dsID'][0]
        obj = self.client.getObject(pid)
        content_model = headers['destination'][len(TOPIC_PREFIX):]
        if content_model in self.contentModels:
            logging.info('Running rules for %(pid)s from %(cmodel)s.' % {'pid': obj.pid, 'cmodel': content_model} )
            for plugin in self.contentModels[content_model]: 
                plugin.runRules(obj, dsID)
        return

    def on_error(self, headers, body):
        """
        \see ConnectionListener::on_error
        """
        self.__print_async("ERROR", headers, body)
        
    def on_connected(self, headers, body):
        """
        \see ConnectionListener::on_connected
        """
        self.__print_async("CONNECTED", headers, body)
  
        
    def ack(self, args):
        """
        Required Parameters:
            message-id - the id of the message being acknowledged
        
        Description:
            Acknowledge consumption of a message from a subscription using client
            acknowledgement. When a client has issued a subscribe with an 'ack' flag set to client
            received from that destination will not be considered to have been consumed  (by the server) until
            the message has been acknowledged.
        """
        if not self.transaction_id:
            self.conn.ack(headers = { 'message-id' : args[1]})
        else:
            self.conn.ack(headers = { 'message-id' : args[1]}, transaction=self.transaction_id)
        
    def abort(self, args):
        """
        Description:
            Roll back a transaction in progress.
        """
        if self.transaction_id:
            self.conn.abort(transaction=self.transaction_id)
            self.transaction_id = None
    
    def begin(self, args):
        """
        Description
            Start a transaction. Transactions in this case apply to sending and acknowledging
            any messages sent or acknowledged during a transaction will be handled atomically based on teh
            transaction.
        """
        if not self.transaction_id:
            self.transaction_id = self.conn.begin()
    
    def commit(self, args):
        """
        Description:
            Commit a transaction in progress.
        """
        if self.transaction_id:
            self.conn.commit(transaction=self.transaction_id)
            self.transaction_id = None
    
    def disconnect(self, args):
        """
        Description:
            Gracefully disconnect from the server.
        """
        try:
            self.conn.disconnect()
        except NotConnectedException:
            pass
    
    def send(self, destination, correlation_id, message):
        """
        Required Parametes:
            destination - where to send the message
            message - the content to send
            
        Description:
        Sends a message to a destination in the message system.
        """
        self.conn.send(destination=destination, message=message, headers={'correlation-id': correlation_id})
        
    def subscribe(self, destination, ack='auto'):
        """
        Required Parameters:
            destination - the name to subscribe to
            
        Optional Parameters:
            ack - how to handle acknowledgements for a message, either automatically (auto) or manually (client)
            
        Description
            Register to listen to a given destination. Like send, the subscribe command requires a destination
            header indicating which destination to subscribe to.  The ack parameter is optional, and defaults to auto.
        """
        self.conn.subscribe(destination=destination, ack=ack)
        
    def unsubscribe(self, destination):
        """
        Required Parameters:
            destination - the name to unsubscribe from
        
        Description:
            Remove an existing subscription - so that the client no longer receives messages from that destination.
        """
        self.conn.unsubscribe(destination)
        
if __name__ == '__main__':
    config = ConfigParser.ConfigParser({'hostname': 'localhost', 'port': '61613', 'username': 'fedoraAdmin', 'password': 'fedoraAdmin',
                                                      'log_file': 'fedora_listener.log', 'log_level': 'INFO',
                                                      'url': 'http://localhost:8080/fedora',
                                                      'models': ''})

    if os.path.exists('/etc/%(conf)s' % {'conf': CONFIG_FILE_NAME}):
        config.read('/etc/%(conf)s' % {'conf': CONFIG_FILE_NAME})
    if os.path.exists(os.path.expanduser('~/.fedora_microservices/%(conf)s' % {'conf': CONFIG_FILE_NAME})):
        config.read('/etc/%(conf)s' % {'conf': CONFIG_FILE_NAME})
    if os.path.exists(CONFIG_FILE_NAME):
        config.read(CONFIG_FILE_NAME)
        
    log_filename = config.get('Logging', 'log_file')
    levels = {'DEBUG':logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR':logging.ERROR, 'CRITICAL':logging.CRITICAL, 'FATAL':logging.FATAL}
    logging.basicConfig(filename = log_filename, level = levels[config.get('Logging', 'log_level')])
    parser = OptionParser()
    models = [v.strip() for v in config.get('ContentModels', 'models').split(',')]

    parser.add_option('-H', '--stomphost', type = 'string', dest = 'host', default = config.get('MessagingServer', 'hostname'),
                      help = 'Hostname or IP to connect to. Defaults to localhost if not specified.')
    parser.add_option('-P', '--stompport', type = 'int', dest = 'port', default = config.get('MessagingServer', 'port'),
                      help = 'Port providing stomp protocol connections. Defaults to 61613 if not specified.')
    parser.add_option('-U', '--user', type = 'string', dest = 'user', default = config.get('MessagingServer', 'username'),
                      help = 'Username for the connection')
    parser.add_option('-W', '--password', type = 'string', dest = 'password', default = config.get('MessagingServer', 'password'),
                      help = 'Password for the connection')
    parser.add_option('-M', '--cmodel', type = 'string', action = 'append', dest = 'cmodels',
                      help = 'Content model. Can be repeated')
    parser.add_option('-R', '--fedoraurl', type = 'string', dest = 'fedoraurl', default = config.get('RepositoryServer', 'url'),
                      help = 'Fedora URL. Defaults to http://localhost:8080/fedora')
    (options, args) = parser.parse_args()
    sf = ContentModelListener(options.cmodels, options.host, options.port, options.user, options.password, options.fedoraurl)
    
    if not options.cmodels:
        options.cmodels = models
    for model in options.cmodels:
        sf.subscribe("/topic/fedora.contentmodel.%s" % (model))
        logging.info("Subscribing to topic /topic/fedora.contentmodel.%(model)s." % {'model': model})
