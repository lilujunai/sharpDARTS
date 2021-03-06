import os
import sys
import time
import glob
import json
import copy
import numpy as np
import torch
import utils
import logging
import argparse
import torch.nn as nn
import torch.utils
import torchvision.datasets as dset
import torch.backends.cudnn as cudnn

from torch.autograd import Variable
from model import NetworkCIFAR
from model import NetworkImageNet
from tqdm import tqdm

import genotypes
import operations
import cifar10_1
import dataset
import flops_counter
from cosine_power_annealing import cosine_power_annealing
from model import MultiChannelNetworkModel

def main():
  parser = argparse.ArgumentParser("Common Argument Parser")
  parser.add_argument('--data', type=str, default='../data', help='location of the data corpus')
  parser.add_argument('--dataset', type=str, default='cifar10', help='which dataset:\
                      cifar10, mnist, emnist, fashion, svhn, stl10, devanagari')
  parser.add_argument('--batch_size', type=int, default=64, help='batch size')
  parser.add_argument('--learning_rate', type=float, default=0.025, help='init learning rate')
  parser.add_argument('--learning_rate_min', type=float, default=1e-8, help='min learning rate')
  parser.add_argument('--lr_power_annealing_exponent_order', type=float, default=2,
                      help='Cosine Power Annealing Schedule Base, larger numbers make '
                           'the exponential more dominant, smaller make cosine more dominant, '
                           '1 returns to standard cosine annealing.')
  parser.add_argument('--momentum', type=float, default=0.9, help='momentum')
  parser.add_argument('--weight_decay', '--wd', dest='weight_decay', type=float, default=3e-4, help='weight decay')
  parser.add_argument('--partial', default=1/8, type=float, help='partially adaptive parameter p in Padam')
  parser.add_argument('--report_freq', type=float, default=50, help='report frequency')
  parser.add_argument('--gpu', type=int, default=0, help='gpu device id')
  parser.add_argument('--epochs', type=int, default=2000, help='num of training epochs')
  parser.add_argument('--start_epoch', default=1, type=int, metavar='N',
                      help='manual epoch number (useful for restarts)')
  parser.add_argument('--warmup_epochs', type=int, default=5, help='num of warmup training epochs')
  parser.add_argument('--warm_restarts', type=int, default=20, help='warm restarts of cosine annealing')
  parser.add_argument('--init_channels', type=int, default=36, help='num of init channels')
  parser.add_argument('--mid_channels', type=int, default=32, help='C_mid channels in choke SharpSepConv')
  parser.add_argument('--layers', type=int, default=20, help='total number of layers')
  parser.add_argument('--model_path', type=str, default='saved_models', help='path to save the model')
  parser.add_argument('--auxiliary', action='store_true', default=False, help='use auxiliary tower')
  parser.add_argument('--mixed_auxiliary', action='store_true', default=False, help='Learn weights for auxiliary networks during training. Overrides auxiliary flag')
  parser.add_argument('--auxiliary_weight', type=float, default=0.4, help='weight for auxiliary loss')
  parser.add_argument('--cutout', action='store_true', default=False, help='use cutout')
  parser.add_argument('--cutout_length', type=int, default=16, help='cutout length')
  parser.add_argument('--autoaugment', action='store_true', default=False, help='use cifar10 autoaugment https://arxiv.org/abs/1805.09501')
  parser.add_argument('--random_eraser', action='store_true', default=False, help='use random eraser')
  parser.add_argument('--drop_path_prob', type=float, default=0.2, help='drop path probability')
  parser.add_argument('--save', type=str, default='EXP', help='experiment name')
  parser.add_argument('--seed', type=int, default=0, help='random seed')
  parser.add_argument('--arch', type=str, default='DARTS', help='which architecture to use')
  parser.add_argument('--ops', type=str, default='OPS', help='which operations to use, options are OPS and DARTS_OPS')
  parser.add_argument('--primitives', type=str, default='PRIMITIVES',
                      help='which primitive layers to use inside a cell search space,'
                           ' options are PRIMITIVES, SHARPER_PRIMITIVES, and DARTS_PRIMITIVES')
  parser.add_argument('--optimizer', type=str, default='sgd', help='which optimizer to use, options are padam and sgd')
  parser.add_argument('--load', type=str, default='',  metavar='PATH', help='load weights at specified location')
  parser.add_argument('--grad_clip', type=float, default=5, help='gradient clipping')
  parser.add_argument('--flops', action='store_true', default=False, help='count flops and exit, aka floating point operations.')
  parser.add_argument('-e', '--evaluate', dest='evaluate', type=str, metavar='PATH', default='',
                      help='evaluate model at specified path on training, test, and validation datasets')
  parser.add_argument('--multi_channel', action='store_true', default=False, help='perform multi channel search, a completely separate search space')
  parser.add_argument('--load_args', type=str, default='',  metavar='PATH',
                      help='load command line args from a json file, this will override '
                           'all currently set args except for --evaluate, and arguments '
                           'that did not exist when the json file was originally saved out.')
  parser.add_argument('--layers_of_cells', type=int, default=8, help='total number of cells in the whole network, default is 8 cells')
  parser.add_argument('--layers_in_cells', type=int, default=4,
                      help='Total number of nodes in each cell, aka number of steps,'
                           ' default is 4 nodes, which implies 8 ops')
  parser.add_argument('--weighting_algorithm', type=str, default='scalar',
                    help='which operations to use, options are '
                         '"max_w" (1. - max_w + w) * op, and scalar (w * op)')
  # TODO(ahundt) remove final path and switch back to genotype
  parser.add_argument('--load_genotype', type=str, default=None, help='Name of genotype to be used')
  parser.add_argument('--simple_path', default=True, action='store_false', help='Final model is a simple path (MultiChannelNetworkModel)')
  args = parser.parse_args()

  args = utils.initialize_files_and_args(args)

  logger = utils.logging_setup(args.log_file_path)

  if not torch.cuda.is_available():
    logger.info('no gpu device available')
    sys.exit(1)

  np.random.seed(args.seed)
  torch.cuda.set_device(args.gpu)
  cudnn.benchmark = True
  torch.manual_seed(args.seed)
  cudnn.enabled=True
  torch.cuda.manual_seed(args.seed)
  logger.info('gpu device = %d' % args.gpu)
  logger.info("args = %s", args)

  DATASET_CLASSES = dataset.class_dict[args.dataset]
  DATASET_CHANNELS = dataset.inp_channel_dict[args.dataset]
  DATASET_MEAN = dataset.mean_dict[args.dataset]
  DATASET_STD = dataset.std_dict[args.dataset]
  logger.info('output channels: ' + str(DATASET_CLASSES))

  # # load the correct ops dictionary
  op_dict_to_load = "operations.%s" % args.ops
  logger.info('loading op dict: ' + str(op_dict_to_load))
  op_dict = eval(op_dict_to_load)

  # load the correct primitives list
  primitives_to_load = "genotypes.%s" % args.primitives
  logger.info('loading primitives:' + primitives_to_load)
  primitives = eval(primitives_to_load)
  logger.info('primitives: ' + str(primitives))

  genotype = eval("genotypes.%s" % args.arch)
  # create the neural network

  criterion = nn.CrossEntropyLoss()
  criterion = criterion.cuda()
  if args.multi_channel:
    final_path = None
    if args.load_genotype is not None:
      genotype = getattr(genotypes, args.load_genotype)
      print(genotype)
      if type(genotype[0]) is str:
        logger.info('Path :%s', genotype)
    # TODO(ahundt) remove final path and switch back to genotype
    cnn_model = MultiChannelNetwork(
      args.init_channels, DATASET_CLASSES, layers=args.layers_of_cells, criterion=criterion, steps=args.layers_in_cells,
      weighting_algorithm=args.weighting_algorithm, genotype=genotype)
    flops_shape = [1, 3, 32, 32]
  elif args.dataset == 'imagenet':
      cnn_model = NetworkImageNet(args.init_channels, DATASET_CLASSES, args.layers, args.auxiliary, genotype, op_dict=op_dict, C_mid=args.mid_channels)
      flops_shape = [1, 3, 224, 224]
  else:
      cnn_model = NetworkCIFAR(args.init_channels, DATASET_CLASSES, args.layers, args.auxiliary, genotype, op_dict=op_dict, C_mid=args.mid_channels)
      flops_shape = [1, 3, 32, 32]
  cnn_model = cnn_model.cuda()

  logger.info("param size = %fMB", utils.count_parameters_in_MB(cnn_model))
  if args.flops:
    logger.info('flops_shape = ' + str(flops_shape))
    logger.info("flops = " + utils.count_model_flops(cnn_model, data_shape=flops_shape))
    return

  optimizer = torch.optim.SGD(
      cnn_model.parameters(),
      args.learning_rate,
      momentum=args.momentum,
      weight_decay=args.weight_decay
      )

  # Get preprocessing functions (i.e. transforms) to apply on data
  train_transform, valid_transform = utils.get_data_transforms(args)
  if args.evaluate:
    # evaluate the train dataset without augmentation
    train_transform = valid_transform

  # Get the training queue, use full training and test set
  train_queue, valid_queue = dataset.get_training_queues(
    args.dataset, train_transform, valid_transform, args.data, args.batch_size, train_proportion=1.0, search_architecture=False)

  test_queue = None
  if args.dataset == 'cifar10':
    # evaluate best model weights on cifar 10.1
    # https://github.com/modestyachts/CIFAR-10.1
    test_data = cifar10_1.CIFAR10_1(root=args.data, download=True, transform=valid_transform)
    test_queue = torch.utils.data.DataLoader(
      test_data, batch_size=args.batch_size, shuffle=False, pin_memory=True, num_workers=8)

  if args.evaluate:
    # evaluate the loaded model, print the result, and return
    logger.info("Evaluating inference with weights file: " + args.load)
    eval_stats = evaluate(
      args, cnn_model, criterion, args.load,
      train_queue=train_queue, valid_queue=valid_queue, test_queue=test_queue)
    with open(args.stats_file, 'w') as f:
      arg_dict = vars(args)
      arg_dict.update(eval_stats)
      json.dump(arg_dict, f)
    logger.info("flops = " + utils.count_model_flops(cnn_model))
    logger.info(utils.dict_to_log_string(eval_stats))
    logger.info('\nEvaluation of Loaded Model Complete! Save dir: ' + str(args.save))
    return

  lr_schedule = cosine_power_annealing(
    epochs=args.epochs, max_lr=args.learning_rate, min_lr=args.learning_rate_min,
    warmup_epochs=args.warmup_epochs, exponent_order=args.lr_power_annealing_exponent_order)
  epochs = np.arange(args.epochs) + args.start_epoch
  # scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, float(args.epochs))
  epoch_stats = []

  stats_csv = args.epoch_stats_file
  stats_csv = stats_csv.replace('.json', '.csv')
  with tqdm(epochs, dynamic_ncols=True) as prog_epoch:
    best_valid_acc = 0.0
    best_epoch = 0
    best_stats = {}
    stats = {}
    epoch_stats = []
    weights_file = os.path.join(args.save, 'weights.pt')
    for epoch, learning_rate in zip(prog_epoch, lr_schedule):
      # update the drop_path_prob augmentation
      cnn_model.drop_path_prob = args.drop_path_prob * epoch / args.epochs
      # update the learning rate
      for param_group in optimizer.param_groups:
        param_group['lr'] = learning_rate
      # scheduler.get_lr()[0]

      train_acc, train_obj = train(args, train_queue, cnn_model, criterion, optimizer)

      val_stats = infer(args, valid_queue, cnn_model, criterion)
      stats.update(val_stats)
      stats['train_acc'] = train_acc
      stats['train_loss'] = train_obj
      stats['lr'] = learning_rate
      stats['epoch'] = epoch

      if stats['valid_acc'] > best_valid_acc:
        # new best epoch, save weights
        utils.save(cnn_model, weights_file)
        best_epoch = epoch
        best_stats.update(copy.deepcopy(stats))
        best_valid_acc = stats['valid_acc']
        best_train_loss = train_obj
        best_train_acc = train_acc
      # else:
      #   # not best epoch, load best weights
      #   utils.load(cnn_model, weights_file)
      logger.info('epoch, %d, train_acc, %f, valid_acc, %f, train_loss, %f, valid_loss, %f, lr, %e, best_epoch, %d, best_valid_acc, %f, ' + utils.dict_to_log_string(stats),
                  epoch, train_acc, stats['valid_acc'], train_obj, stats['valid_loss'], learning_rate, best_epoch, best_valid_acc)
      stats['train_acc'] = train_acc
      stats['train_loss'] = train_obj
      epoch_stats += [copy.deepcopy(stats)]
      with open(args.epoch_stats_file, 'w') as f:
        json.dump(epoch_stats, f, cls=utils.NumpyEncoder)
      utils.list_of_dicts_to_csv(stats_csv, epoch_stats)

    # get stats from best epoch including cifar10.1
    eval_stats = evaluate(args, cnn_model, criterion, weights_file, train_queue, valid_queue, test_queue)
    with open(args.stats_file, 'w') as f:
      arg_dict = vars(args)
      arg_dict.update(eval_stats)
      json.dump(arg_dict, f, cls=utils.NumpyEncoder)
    with open(args.epoch_stats_file, 'w') as f:
      json.dump(epoch_stats, f, cls=utils.NumpyEncoder)
    logger.info(utils.dict_to_log_string(eval_stats))
    logger.info('Training of Final Model Complete! Save dir: ' + str(args.save))

def evaluate(args, cnn_model, criterion, weights_file=None, train_queue=None, valid_queue=None, test_queue=None, prefix='best_'):
  # load the best model weights
  if weights_file is not None:
    utils.load(cnn_model, weights_file)
  test_prefix = 'test_'
  if args.dataset == 'cifar10':
    test_prefix = 'cifar10_1_test_'
  queues = [train_queue, valid_queue, test_queue]
  stats = {}
  with tqdm(['train_', 'valid_', test_prefix], desc='Final Evaluation', dynamic_ncols=True) as prefix_progbar:
    for dataset_prefix, queue in zip(prefix_progbar, queues):
      if queue is not None:
        stats.update(infer(args, queue, cnn_model, criterion=criterion, prefix=prefix + dataset_prefix, desc='Running ' + dataset_prefix + 'data'))
  return stats


def train(args, train_queue, cnn_model, criterion, optimizer):
  objs = utils.AvgrageMeter()
  top1 = utils.AvgrageMeter()
  top5 = utils.AvgrageMeter()
  cnn_model.train()

  with tqdm(train_queue, dynamic_ncols=True) as progbar:
    for step, (input_batch, target) in enumerate(progbar):
      input_batch = Variable(input_batch).cuda(non_blocking=True)
      target = Variable(target).cuda(non_blocking=True)

      optimizer.zero_grad()
      if args.auxiliary:
        logits, logits_aux = cnn_model(input_batch)
      else:
         logits = cnn_model(input_batch)
         logits_aux = None
      loss = criterion(logits, target)
      if logits_aux is not None and args.auxiliary:
        loss_aux = criterion(logits_aux, target)
        loss += args.auxiliary_weight * loss_aux
      loss.backward()
      nn.utils.clip_grad_norm_(cnn_model.parameters(), args.grad_clip)
      # if cnn_model.auxs is not None:
      #   # clip the aux weights even more so they don't jump too quickly
      #   nn.utils.clip_grad_norm_(cnn_model.auxs.alphas, args.grad_clip/10)
      optimizer.step()

      prec1, prec5 = utils.accuracy(logits, target, topk=(1, 5))
      n = input_batch.size(0)
      objs.update(loss.data.item(), n)
      top1.update(prec1.data.item(), n)
      top5.update(prec5.data.item(), n)

      progbar.set_description('Training loss: {0:9.5f}, top 1: {1:5.2f}, top 5: {2:5.2f} progress'.format(objs.avg, top1.avg, top5.avg))

  return top1.avg, objs.avg


def infer(args, valid_queue, cnn_model, criterion, prefix='valid_', desc='Running Validation'):
  objs = utils.AvgrageMeter()
  top1 = utils.AvgrageMeter()
  top5 = utils.AvgrageMeter()
  cnn_model.eval()

  with torch.no_grad():
    # dynamic_ncols = false in this case because we want accurate timing stats
    with tqdm(valid_queue, dynamic_ncols=False, desc=desc) as progbar:
      for step, (input_batch, target) in enumerate(progbar):
        input_batch = Variable(input_batch).cuda(non_blocking=True)
        target = Variable(target).cuda(non_blocking=True)

        if args.auxiliary:
          logits, _ = cnn_model(input_batch)
        else:
          logits = cnn_model(input_batch)
        loss = criterion(logits, target)

        prec1, prec5 = utils.accuracy(logits, target, topk=(1, 5))
        n = input_batch.size(0)
        objs.update(loss.data.item(), n)
        top1.update(prec1.data.item(), n)
        top5.update(prec5.data.item(), n)
        # description on each validation step is disabled for performance reasons
        # progbar.set_description('Validation step: {0}, loss: {1:9.5f}, top 1: {2:5.2f} top 5: {3:5.2f} progress'.format(step, objs.avg, top1.avg, top5.avg))
      # extract progbar timing stats from tqdm https://github.com/tqdm/tqdm/issues/660
      stats = utils.tqdm_stats(progbar, prefix=prefix)
      stats[prefix + 'acc'] = top1.avg
      stats[prefix + 'loss'] = objs.avg
      stats[prefix + 'top1'] = top1.avg
      stats[prefix + 'top5'] = top5.avg
  # return top1, avg loss, and timing stats string
  return stats


if __name__ == '__main__':
  main()

